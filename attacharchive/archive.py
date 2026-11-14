"""附件归档：固定大小分片、断点续传、原子合并与清单对账。

存储布局（root 下）：
    objects/<key>   合并完成的对象
    chunks/<key>/   上传中的分片与 state.json（断点续传状态）
    tmp/            合并临时对象，os.replace 原子改名到 objects/
    manifest.json   归档清单，与对象提交保持两阶段事务
"""
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024

_OBJECTS = "objects"
_CHUNKS = "chunks"
_TMP = "tmp"
_MANIFEST = "manifest.json"
_STATE = "state.json"


class ArchiveError(Exception):
    """分片缺失、校验失败等归档错误。"""


def _check_key(key):
    parts = key.replace("\\", "/").split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError("invalid key: %r" % (key,))


def _object_path(root, key):
    return os.path.join(root, _OBJECTS, *key.split("/"))


def _chunk_dir(root, key):
    return os.path.join(root, _CHUNKS, *key.split("/"))


def _file_sha256(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _load_manifest(root):
    try:
        with open(os.path.join(root, _MANIFEST), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"files": {}}
    data.setdefault("files", {})
    return data


def _save_manifest(root, manifest):
    os.makedirs(root, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    _write_atomic(os.path.join(root, _MANIFEST), payload.encode("utf-8"))


def _load_state(root, key):
    try:
        with open(os.path.join(_chunk_dir(root, key), _STATE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _save_state(root, key, state):
    payload = json.dumps(state, sort_keys=True)
    _write_atomic(os.path.join(_chunk_dir(root, key), _STATE), payload.encode("utf-8"))


def _read_chunk(stream, size):
    buf = bytearray()
    while len(buf) < size:
        piece = stream.read(size - len(buf))
        if not piece:
            break
        buf += piece
    return bytes(buf) if buf else None


def _store_chunk(chunk_dir, index, data, digest):
    path = os.path.join(chunk_dir, "%08d.part" % index)
    if os.path.exists(path) and _file_sha256(path) == digest:
        return "resumed"
    _write_atomic(path, data)
    return "written"


def _merge(root, key):
    """按序号拼接分片，逐片校验并核对整体哈希，原子改名到 objects/。"""
    state = _load_state(root, key)
    if not state or state.get("sha256") is None:
        raise ArchiveError("no complete chunk state for %r" % (key,))
    chunk_dir = _chunk_dir(root, key)
    tmp_path = os.path.join(root, _TMP, *key.split("/")) + ".tmp"
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
    hasher = hashlib.sha256()
    try:
        with open(tmp_path, "wb") as out:
            for index, digest in enumerate(state["hashes"]):
                path = os.path.join(chunk_dir, "%08d.part" % index)
                if not os.path.exists(path):
                    raise ArchiveError("missing chunk %d for %r" % (index, key))
                with open(path, "rb") as handle:
                    data = handle.read()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ArchiveError("chunk %d corrupt for %r" % (index, key))
                out.write(data)
                hasher.update(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    if hasher.hexdigest() != state["sha256"]:
        os.remove(tmp_path)
        raise ArchiveError("overall hash mismatch for %r" % (key,))
    target = _object_path(root, key)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(tmp_path, target)
    return target


def upload(key, stream, root="archive", chunk_size=DEFAULT_CHUNK_SIZE, workers=4):
    """分片上传：固定大小切片、逐片校验、断点续传、并发写片、原子合并。"""
    _check_key(key)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    target = _object_path(root, key)
    entry = _load_manifest(root)["files"].get(key)
    if entry and os.path.exists(target) and _file_sha256(target) == entry["sha256"]:
        shutil.rmtree(_chunk_dir(root, key), ignore_errors=True)
        return {"key": key, "size": entry["size"], "path": target,
                "sha256": entry["sha256"], "parts": entry["parts"],
                "resumed": entry["parts"], "retransmitted": 0, "status": "exists"}

    state = _load_state(root, key)
    if state and state.get("chunk_size") != chunk_size:
        shutil.rmtree(_chunk_dir(root, key), ignore_errors=True)
        state = None
    hashes = list(state.get("hashes", [])) if state else []
    chunk_dir = _chunk_dir(root, key)
    os.makedirs(chunk_dir, exist_ok=True)

    overall = hashlib.sha256()
    size = 0
    resumed = 0
    retransmitted = 0

    def collect(pending):
        nonlocal resumed, retransmitted
        for index, digest, future in pending:
            if future.result() == "resumed":
                resumed += 1
            else:
                retransmitted += 1
            while len(hashes) <= index:
                hashes.append(None)
            hashes[index] = digest
        if pending:
            _save_state(root, key, {"chunk_size": chunk_size,
                                    "sha256": None, "hashes": hashes})
        pending.clear()

    pending = []
    index = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        while True:
            data = _read_chunk(stream, chunk_size)
            if data is None:
                break
            digest = hashlib.sha256(data).hexdigest()
            overall.update(data)
            pending.append((index, digest,
                            pool.submit(_store_chunk, chunk_dir, index, data, digest)))
            size += len(data)
            index += 1
            if len(pending) >= max(1, workers):
                collect(pending)
        collect(pending)
    del hashes[index:]

    digest = overall.hexdigest()
    _save_state(root, key, {"chunk_size": chunk_size, "sha256": digest, "hashes": hashes})
    path = _merge(root, key)
    manifest = _load_manifest(root)
    manifest["files"][key] = {"size": size, "sha256": digest,
                              "parts": len(hashes), "chunk_size": chunk_size}
    _save_manifest(root, manifest)
    shutil.rmtree(chunk_dir, ignore_errors=True)
    return {"key": key, "size": size, "path": path, "sha256": digest,
            "parts": len(hashes), "resumed": resumed,
            "retransmitted": retransmitted, "status": "completed"}


def reconcile(manifest, root="archive", repair=False):
    """对账：比对期望清单、归档清单和对象库，输出差异；repair=True 只补差异。

    只审计带 sha256 的条目；裸 key 无法校验，视为声明跳过。
    """
    stored = _load_manifest(root)["files"]
    expected = {}
    for entry in manifest or []:
        if isinstance(entry, dict) and entry.get("key") and entry.get("sha256"):
            expected[entry["key"]] = entry
    obj_dir = os.path.join(root, _OBJECTS)
    objects = set()
    for dirpath, _dirs, files in os.walk(obj_dir):
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), obj_dir)
            objects.add(rel.replace(os.sep, "/"))
    known = set(stored) | set(expected)
    missing = []
    corrupt = []
    for key in sorted(known):
        digest = stored.get(key, {}).get("sha256") or expected[key].get("sha256")
        if key not in objects:
            missing.append(key)
        elif digest and _file_sha256(_object_path(root, key)) != digest:
            corrupt.append(key)
    orphans = sorted(objects - known)
    result = {"missing": missing, "orphans": orphans, "corrupt": corrupt}
    if repair:
        repaired = []
        for key in orphans + corrupt:
            os.remove(_object_path(root, key))
            repaired.append(key)
        result["repaired"] = repaired
    return result
