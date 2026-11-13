"""采集附件归档：内容哈希命名、流式读写、中断续传、引用计数去重、清单校验恢复。"""
import hashlib
import json
import os
import re

CHUNK_SIZE = 64 * 1024
SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def pack(files, root):
    """打包一批附件，返回 {"added", "reused", "failed", "paths", "entries"}。"""
    objects_dir = os.path.join(root, "objects")
    tmp_dir = os.path.join(root, ".tmp")
    os.makedirs(objects_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    manifest_path = os.path.join(root, "manifest.json")
    refs_path = os.path.join(root, "refs.json")
    manifest = _load_json(manifest_path, {"version": 1, "files": []})
    refs = _load_json(refs_path, {})
    stats = {"added": 0, "reused": 0, "failed": 0, "paths": [], "entries": []}
    for item in files:
        try:
            entry, reused = _store(item, root, tmp_dir)
        except Exception:
            stats["failed"] += 1
            continue
        refs[entry["sha256"]] = refs.get(entry["sha256"], 0) + 1
        manifest["files"].append(entry)
        stats["reused" if reused else "added"] += 1
        stats["paths"].append(os.path.join(root, entry["path"]))
        stats["entries"].append(entry)
    _dump_json(manifest_path, manifest)
    _dump_json(refs_path, refs)
    return stats


def restore(root):
    """按清单逐条校验，返回带 status(ok/missing/corrupt) 的记录。"""
    manifest = _load_json(os.path.join(root, "manifest.json"), {"files": []})
    rows = []
    for entry in manifest.get("files", []):
        row = dict(entry)
        path = os.path.join(root, entry.get("path", ""))
        if not os.path.isfile(path):
            row["status"] = "missing"
        elif os.path.getsize(path) != entry.get("size"):
            row["status"] = "corrupt"
        elif _hash_file(path) != entry.get("sha256"):
            row["status"] = "corrupt"
        else:
            row["status"] = "ok"
        rows.append(row)
    return rows


def _store(item, root, tmp_dir):
    """流式写入 .part 并增量算哈希，完成后原子改名；已存在则复用。"""
    url = item["url"]
    name = _safe_name(url)
    ext = _safe_ext(name)
    part = os.path.join(tmp_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".part")
    data = item.get("data", b"")
    stream = data if hasattr(data, "read") else None
    if stream is None and isinstance(data, str):
        data = data.encode("utf-8")
    offset = _resume_offset(part, stream, None if stream is not None else data)
    hasher = hashlib.sha256()
    size = offset
    if offset:
        with open(part, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                hasher.update(chunk)
        if stream is not None:
            stream.seek(offset)
    with open(part, "ab" if offset else "wb") as out:
        if stream is None:
            view = memoryview(data)[offset:]
            for start in range(0, len(view), CHUNK_SIZE):
                size += _write(out, hasher, bytes(view[start:start + CHUNK_SIZE]))
        else:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += _write(out, hasher, chunk)
    digest = hasher.hexdigest()
    rel = os.path.join("objects", digest[:2], digest + ext)
    dst = os.path.join(root, rel)
    if os.path.exists(dst):
        os.remove(part)
        reused = True
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(part, dst)
        reused = False
    return {"url": url, "name": name, "size": size, "sha256": digest, "path": rel}, reused


def _resume_offset(part, stream, data):
    """已有 .part 且来源可定位（bytes 或可 seek 流）时从中断处续传。"""
    if not os.path.exists(part):
        return 0
    size = os.path.getsize(part)
    if stream is not None:
        try:
            return size if stream.seekable() else 0
        except Exception:
            return 0
    return size if size <= len(data) else 0


def _write(out, hasher, chunk):
    if isinstance(chunk, str):
        chunk = chunk.encode("utf-8")
    out.write(chunk)
    hasher.update(chunk)
    return len(chunk)


def _safe_name(url):
    """去掉查询参数和锚点，只保留安全字符，禁止路径穿越。"""
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    base = path.rsplit("/", 1)[-1] or "index"
    base = SAFE_CHARS.sub("_", base.replace("..", "_")).strip("._")
    return (base or "index")[:64]


def _safe_ext(name):
    ext = os.path.splitext(name)[1].lower()
    return ext if SAFE_EXT.match(ext) else ""


def _hash_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _dump_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
