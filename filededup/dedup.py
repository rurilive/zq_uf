"""内容寻址的文件去重与引用回收。

存储布局（root 下只有 objects/ 和 index.json）：
  objects/<sha256>   按内容哈希保存的对象，全仓库只存一份
  index.json         {path: object_id} 的路径索引；同一 object_id 出现
                     几次，引用计数就是几，引用路径可反查

add/remove 持进程内线程锁并对 root 目录加文件锁串行化，保证不会
删掉其他路径正在使用的对象。
"""
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 无 fcntl，仅靠线程锁
    fcntl = None

OBJECTS_DIR = "objects"
INDEX_FILE = "index.json"
CHUNK_SIZE = 1024 * 1024


class _RootLock:
    """同一 root 的线程锁 + 跨进程文件锁。"""

    _thread_locks = {}
    _guard = threading.Lock()

    def __init__(self, root):
        self.root = os.path.abspath(root)
        with self._guard:
            self._thread_lock = self._thread_locks.setdefault(
                self.root, threading.Lock()
            )
        self._dir_fd = None

    def __enter__(self):
        self._thread_lock.acquire()
        os.makedirs(self.root, exist_ok=True)
        self._dir_fd = os.open(self.root, os.O_RDONLY)
        if fcntl is not None:
            fcntl.flock(self._dir_fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if fcntl is not None and self._dir_fd is not None:
            fcntl.flock(self._dir_fd, fcntl.LOCK_UN)
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None
        self._thread_lock.release()


class Store:
    """内容寻址仓库：对象按哈希存一份，路径以引用方式指向对象。"""

    def __init__(self, root="store"):
        self.root = root

    def _paths(self):
        index_path = os.path.join(self.root, INDEX_FILE)
        if not os.path.exists(index_path):
            return {}
        with open(index_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_index(self, paths):
        fd, tmp_name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(paths, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, os.path.join(self.root, INDEX_FILE))
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    def _drop_index(self, paths):
        index_path = os.path.join(self.root, INDEX_FILE)
        if paths:
            self._write_index(paths)
        elif os.path.exists(index_path):
            os.remove(index_path)

    @staticmethod
    def _object_path(root, object_id):
        return os.path.join(root, OBJECTS_DIR, object_id)

    @staticmethod
    def _hash_file(path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_object(self, object_id, source):
        objects_dir = os.path.join(self.root, OBJECTS_DIR)
        os.makedirs(objects_dir, exist_ok=True)
        dest = self._object_path(self.root, object_id)
        if os.path.exists(dest):
            return False
        fd, tmp_name = tempfile.mkstemp(dir=objects_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
                shutil.copyfileobj(inp, out, CHUNK_SIZE)
            try:
                shutil.copystat(source, tmp_name)
            except OSError:
                pass
            os.replace(tmp_name, dest)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        return True

    def add(self, path):
        """登记 path：同内容复用对象，否则新增对象。

        返回 dict：object_id、object_path、action(added/reused/remapped)
        以及 references（该对象当前全部引用路径）。
        """
        key = os.path.abspath(path)
        with _RootLock(self.root):
            paths = self._paths()
            object_id = self._hash_file(key)
            old_id = paths.get(key)
            if old_id == object_id:
                action = "reused"
            elif old_id is None:
                action = "reused" if self._is_referenced(paths, object_id) else "added"
            else:
                action = "remapped"
            created = self._write_object(object_id, key)
            if created and action == "reused":
                action = "added"
            paths[key] = object_id
            reclaimed = []
            if old_id is not None and old_id != object_id:
                reclaimed = self._reclaim(paths, old_id)
            self._drop_index(paths)
            return {
                "action": action,
                "object_id": object_id,
                "object_path": self._object_path(self.root, object_id),
                "references": self._refs_of(paths, object_id),
                "reclaimed": reclaimed,
            }

    def remove(self, path):
        """摘除 path 引用；对象引用归零才删除。

        返回 dict：object_id、action(removed/unknown)、reclaimed
        （归零回收的对象，未归零为空）、references（剩余引用路径）。
        """
        key = os.path.abspath(path)
        with _RootLock(self.root):
            paths = self._paths()
            object_id = paths.pop(key, None)
            if object_id is None:
                return {
                    "action": "unknown",
                    "object_id": None,
                    "reclaimed": [],
                    "references": [],
                }
            reclaimed = self._reclaim(paths, object_id)
            self._drop_index(paths)
            return {
                "action": "removed",
                "object_id": object_id,
                "reclaimed": reclaimed,
                "references": self._refs_of(paths, object_id),
            }

    def _reclaim(self, paths, object_id):
        """对象无引用时删除对象文件并返回其 id，否则返回空列表。"""
        if self._refs_of(paths, object_id):
            return []
        object_path = self._object_path(self.root, object_id)
        if os.path.exists(object_path):
            os.remove(object_path)
        objects_dir = os.path.join(self.root, OBJECTS_DIR)
        with contextlib.suppress(OSError):
            os.rmdir(objects_dir)
        return [object_id]

    @staticmethod
    def _refs_of(paths, object_id):
        """按对象反查仍引用它的全部路径。"""
        return sorted(ref for ref, oid in paths.items() if oid == object_id)

    @staticmethod
    def _is_referenced(paths, object_id):
        return any(oid == object_id for oid in paths.values())

    def references(self, object_id=None, path=None):
        """按对象 id（或按路径解析出对象）查引用路径列表。"""
        with _RootLock(self.root):
            paths = self._paths()
            if object_id is None:
                if path is None:
                    raise ValueError("需要 object_id 或 path")
                object_id = paths.get(os.path.abspath(path))
                if object_id is None:
                    return []
            return self._refs_of(paths, object_id)

    def report(self):
        """对象口径快照：objects 里每个对象的 id、路径与引用计数。"""
        with _RootLock(self.root):
            paths = self._paths()
            objects_dir = os.path.join(self.root, OBJECTS_DIR)
            on_disk = (
                sorted(os.listdir(objects_dir))
                if os.path.isdir(objects_dir)
                else []
            )
            referenced = {}
            for ref, object_id in paths.items():
                entry = referenced.setdefault(
                    object_id,
                    {
                        "object_id": object_id,
                        "object_path": self._object_path(self.root, object_id),
                        "refcount": 0,
                        "references": [],
                    },
                )
                entry["references"].append(ref)
            for entry in referenced.values():
                entry["references"].sort()
                entry["refcount"] = len(entry["references"])
            orphans = [oid for oid in on_disk if oid not in referenced]
            return {
                "objects": sorted(
                    referenced.values(), key=lambda item: item["object_id"]
                ),
                "orphans": sorted(orphans),
            }


def add(path, root="store"):
    """向后兼容入口：返回对象路径字符串。"""
    return Store(root).add(path)["object_path"]


def remove(path, root="store"):
    """向后兼容入口：摘除引用，归零回收。"""
    return Store(root).remove(path)["action"] in ("removed", "unknown")


def references(object_id=None, path=None, root="store"):
    """按对象 id（或路径）查引用路径。"""
    return Store(root).references(object_id=object_id, path=path)


def report(root="store"):
    """返回仓库对象与引用快照。"""
    return Store(root).report()
