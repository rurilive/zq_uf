"""内容寻址 + 引用计数的文件级去重回收。

存储口径：
- 对象：文件内容按 SHA-256 存一份，位于 <root>.objects/<hash>。
- 引用：每个 add(path) 在 root 下建指向对象的硬链接，并记入对象 refs。
- 回收：remove(path) 只减引用，归零才删对象。
- 并发：所有增删查由全局锁串行化，不会删到仍被引用的对象。
"""
import hashlib
import os
import shutil
import threading

_LOCK = threading.RLock()
# abspath(root) -> {"objects": {hash: {"path": str, "refs": set}}, "by_path": {path: hash}}
_STORES = {}


def _state(root):
    key = os.path.abspath(root)
    return _STORES.setdefault(key, {"objects": {}, "by_path": {}})


def _objects_dir(root):
    return root.rstrip(os.sep) + ".objects"


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add(path, root="store"):
    """按内容哈希存一份；同内容复用对象并记一条引用。"""
    digest = _hash_file(path)
    dest = os.path.join(root, os.path.basename(path))
    with _LOCK:
        state = _state(root)
        objects = state["objects"]
        by_path = state["by_path"]
        if by_path.get(path) == digest and digest in objects:
            if not os.path.lexists(dest):
                os.makedirs(root, exist_ok=True)
                os.link(objects[digest]["path"], dest)
            return {"path": dest, "object": digest, "created": False,
                    "refcount": len(objects[digest]["refs"])}
        if path in by_path:
            remove(path, root)  # 内容变了，先退掉旧引用
        created = digest not in objects
        if created:
            os.makedirs(_objects_dir(root), exist_ok=True)
            obj_path = os.path.join(_objects_dir(root), digest)
            shutil.copy2(path, obj_path)
            objects[digest] = {"path": obj_path, "refs": set()}
        os.makedirs(root, exist_ok=True)
        if os.path.lexists(dest):
            os.remove(dest)
        os.link(objects[digest]["path"], dest)
        objects[digest]["refs"].add(path)
        by_path[path] = digest
        return {"path": dest, "object": digest, "created": created,
                "refcount": len(objects[digest]["refs"])}


def remove(path, root="store"):
    """只减引用；归零才删对象。返回回收结果（含对象与剩余引用路径）。"""
    dest = os.path.join(root, os.path.basename(path))
    with _LOCK:
        state = _state(root)
        by_path = state["by_path"]
        digest = by_path.get(path)
        if digest is None:
            if os.path.lexists(dest):
                os.remove(dest)
            return {"path": dest, "object": None, "reclaimed": False,
                    "refcount": 0, "paths": []}
        obj = state["objects"][digest]
        obj["refs"].discard(path)
        del by_path[path]
        if os.path.lexists(dest) and os.path.samefile(dest, obj["path"]):
            os.remove(dest)
        reclaimed = not obj["refs"]
        if reclaimed:
            if os.path.exists(obj["path"]):
                os.remove(obj["path"])
            del state["objects"][digest]
        return {"path": dest, "object": digest, "reclaimed": reclaimed,
                "refcount": len(obj["refs"]), "paths": sorted(obj["refs"])}


def objects(root="store"):
    """仍被引用的对象：hash -> 对象路径、引用计数、引用路径。"""
    with _LOCK:
        state = _state(root)
        return {digest: {"path": obj["path"],
                         "refcount": len(obj["refs"]),
                         "paths": sorted(obj["refs"])}
                for digest, obj in state["objects"].items()}


def refs(obj, root="store"):
    """按对象哈希查引用路径。"""
    with _LOCK:
        entry = _state(root)["objects"].get(obj)
        return sorted(entry["refs"]) if entry else []
