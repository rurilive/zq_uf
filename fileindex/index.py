"""数据文件增量指纹索引：按大小与分块 SHA-256 建立指纹。"""
import hashlib
import os

CHUNK_SIZE = 1024 * 1024


class FileIndexError(Exception):
    """索引或校验失败时抛出的明确错误，不静默跳过任何文件。"""


_index = {}


def reset():
    _index.clear()


def _chunk_hashes(path):
    """按 CHUNK_SIZE 分块计算 SHA-256，空文件返回空内容的单个哈希。"""
    chunks = []
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(CHUNK_SIZE)
                if not block:
                    break
                chunks.append(hashlib.sha256(block).hexdigest())
    except OSError as exc:
        raise FileIndexError(f"无法读取文件 {path}: {exc}") from exc
    if not chunks:
        chunks.append(hashlib.sha256(b"").hexdigest())
    return chunks


def _make_entry(path, stat_result):
    return {
        "size": stat_result.st_size,
        "mtime": stat_result.st_mtime,
        "chunks": _chunk_hashes(path),
    }


def _raise_walk_error(exc):
    raise FileIndexError(f"遍历目录失败: {exc}") from exc


def index(root):
    """增量扫描目录，返回新增/变化/删除/跳过统计。

    只清理本次扫描根目录之下、且本轮未出现的索引记录，
    其他目录的记录保持不变，支持按子目录增量更新。
    """
    root_abs = os.path.abspath(root)
    if not os.path.exists(root_abs):
        raise FileIndexError(f"索引根目录不存在: {root_abs}")
    if not os.path.isdir(root_abs):
        raise FileIndexError(f"索引根路径不是目录: {root_abs}")

    stats = {"added": 0, "changed": 0, "deleted": 0, "skipped": 0}
    seen = set()

    for base, _, files in os.walk(root_abs, onerror=_raise_walk_error):
        for name in files:
            path = os.path.abspath(os.path.join(base, name))
            seen.add(path)
            try:
                stat_result = os.stat(path)
            except OSError as exc:
                raise FileIndexError(f"无法获取文件状态 {path}: {exc}") from exc

            previous = _index.get(path)
            if previous is None:
                _index[path] = _make_entry(path, stat_result)
                stats["added"] += 1
                continue

            size_same = previous["size"] == stat_result.st_size
            mtime_same = previous["mtime"] == stat_result.st_mtime
            if size_same and mtime_same:
                stats["skipped"] += 1
                continue

            if not size_same:
                _index[path] = _make_entry(path, stat_result)
                stats["changed"] += 1
                continue

            current_chunks = _chunk_hashes(path)
            if current_chunks == previous["chunks"]:
                previous["mtime"] = stat_result.st_mtime
                stats["skipped"] += 1
            else:
                _index[path] = {
                    "size": stat_result.st_size,
                    "mtime": stat_result.st_mtime,
                    "chunks": current_chunks,
                }
                stats["changed"] += 1

    root_prefix = os.sep if root_abs == os.sep else root_abs + os.sep
    stale = [key for key in _index if key.startswith(root_prefix) and key not in seen]
    for tracked in stale:
        del _index[tracked]
        stats["deleted"] += 1

    stats["indexed"] = len(seen)
    stats["total"] = len(_index)
    return stats


def changed(path):
    """文件相对上次索引是否变化：未建索引或大小/时间不一致即为变化。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileIndexError(f"文件不存在，无法判断变化: {path}")
    previous = _index.get(path)
    if previous is None:
        return True
    try:
        stat_result = os.stat(path)
    except OSError as exc:
        raise FileIndexError(f"无法获取文件状态 {path}: {exc}") from exc
    return (
        stat_result.st_size != previous["size"]
        or stat_result.st_mtime != previous["mtime"]
    )
