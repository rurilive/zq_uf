"""页面快照存储：内容寻址 + 引用计数 + 原子写。"""
import hashlib
import json
import os
import tempfile
import threading

ROOT = "pages"
CHUNK_SIZE = 4096
HEX = set("0123456789abcdef")

_lock = threading.RLock()


def _dirs(root):
    return os.path.join(root, "blocks"), os.path.join(root, "manifests")


def _block_path(root, digest):
    return os.path.join(_dirs(root)[0], digest)


def _manifest_path(root, page_id):
    return os.path.join(_dirs(root)[1], page_id + ".json")


def _refs_path(root):
    return os.path.join(_dirs(root)[0], "refs.json")


def _load_refs(root):
    try:
        with open(_refs_path(root), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_refs(root, refs):
    blocks_dir, _ = _dirs(root)
    fd, tmp = tempfile.mkstemp(dir=blocks_dir, prefix=".refs-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(refs, handle, sort_keys=True)
    os.replace(tmp, _refs_path(root))


def _write_block(root, digest, chunk):
    """块先落临时文件再原子改名；已存在则复用。返回 "new" 或 "reused"。"""
    blocks_dir, _ = _dirs(root)
    path = os.path.join(blocks_dir, digest)
    if os.path.exists(path):
        return "reused"
    fd, tmp = tempfile.mkstemp(dir=blocks_dir, prefix=".blk-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(chunk)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return "reused"
        return "new"
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def put(page_id, content, root=None):
    """写入页面；页面已存在（含并发竞争）时返回冲突，不覆盖。"""
    root = root or ROOT
    blocks_dir, manifests_dir = _dirs(root)
    os.makedirs(blocks_dir, exist_ok=True)
    os.makedirs(manifests_dir, exist_ok=True)
    data = content.encode("utf-8")
    with _lock:
        digests = []
        new_blocks = 0
        reused_blocks = 0
        for offset in range(0, len(data), CHUNK_SIZE):
            chunk = data[offset:offset + CHUNK_SIZE]
            digest = hashlib.sha256(chunk).hexdigest()
            digests.append(digest)
            if _write_block(root, digest, chunk) == "new":
                new_blocks += 1
            else:
                reused_blocks += 1
        manifest = {"page_id": page_id, "encoding": "utf-8", "blocks": digests}
        fd, tmp = tempfile.mkstemp(dir=manifests_dir, prefix=".man-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False)
            try:
                os.link(tmp, _manifest_path(root, page_id))
            except FileExistsError:
                return {"page": page_id, "conflict": True,
                        "new_blocks": 0, "reused_blocks": 0}
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        refs = _load_refs(root)
        for digest in digests:
            refs[digest] = refs.get(digest, 0) + 1
        _save_refs(root, refs)
    return {"page": page_id, "conflict": False,
            "new_blocks": new_blocks, "reused_blocks": reused_blocks}


def get(page_id, root=None):
    root = root or ROOT
    with open(_manifest_path(root, page_id), encoding="utf-8") as handle:
        manifest = json.load(handle)
    parts = []
    for digest in manifest["blocks"]:
        with open(_block_path(root, digest), "rb") as handle:
            parts.append(handle.read())
    return b"".join(parts).decode(manifest.get("encoding", "utf-8"))


def delete(page_id, root=None):
    """删除清单并减块引用，块文件留给 GC 回收。"""
    root = root or ROOT
    with _lock:
        try:
            with open(_manifest_path(root, page_id), encoding="utf-8") as handle:
                manifest = json.load(handle)
            os.remove(_manifest_path(root, page_id))
        except FileNotFoundError:
            return {"page": page_id, "deleted": False, "released_blocks": 0}
        refs = _load_refs(root)
        for digest in manifest["blocks"]:
            refs[digest] = refs.get(digest, 1) - 1
        _save_refs(root, refs)
    return {"page": page_id, "deleted": True,
            "released_blocks": len(manifest["blocks"])}


def gc(root=None):
    """回收引用归零的块；仍被清单引用的块跳过。"""
    root = root or ROOT
    blocks_dir, manifests_dir = _dirs(root)
    with _lock:
        refs = _load_refs(root)
        live = set()
        if os.path.isdir(manifests_dir):
            for name in os.listdir(manifests_dir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(manifests_dir, name),
                              encoding="utf-8") as handle:
                        live.update(json.load(handle)["blocks"])
                except (OSError, ValueError):
                    continue
        reclaimed = []
        skipped = []
        if os.path.isdir(blocks_dir):
            for name in sorted(os.listdir(blocks_dir)):
                if len(name) != 64 or not set(name) <= HEX:
                    continue
                if refs.get(name, 0) > 0:
                    continue
                if name in live:
                    skipped.append(name)
                    continue
                os.remove(os.path.join(blocks_dir, name))
                reclaimed.append(name)
                refs.pop(name, None)
        _save_refs(root, refs)
    return {"reclaimed": reclaimed, "skipped": skipped}
