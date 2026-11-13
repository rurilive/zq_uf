"""数据文件的增量指纹索引：按大小和分块哈希建指纹，未变文件跳过。"""
import hashlib
import os
import stat

DEFAULT_CHUNK_SIZE = 64 * 1024

_index = {}


def reset():
    _index.clear()


def _fingerprint(path, chunk_size):
    """分块流式计算 sha256，避免大文件全量读入内存。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _stat_file(path):
    try:
        return os.stat(path)
    except OSError as e:
        raise OSError(f"无法读取文件状态: {path}: {e}") from e


def index(root, chunk_size=DEFAULT_CHUNK_SIZE):
    """增量扫描目录，返回新增/变化/删除/跳过/总数统计。"""
    root = os.path.abspath(root)
    if not os.path.exists(root):
        raise FileNotFoundError(f"索引根目录不存在: {root}")
    if not os.path.isdir(root):
        raise NotADirectoryError(f"索引根路径不是目录: {root}")
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError(f"chunk_size 必须为正整数: {chunk_size!r}")

    added = changed_count = deleted = skipped = 0
    seen = set()

    def _raise(error):
        raise error

    for base, _, files in os.walk(root, onerror=_raise):
        for name in files:
            path = os.path.abspath(os.path.join(base, name))
            st = _stat_file(path)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"非常规文件，无法建立指纹: {path}")
            seen.add(path)
            record = _index.get(path)
            if (
                record is not None
                and record["size"] == st.st_size
                and record["mtime_ns"] == st.st_mtime_ns
            ):
                skipped += 1
                continue
            _index[path] = {
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "hash": _fingerprint(path, chunk_size),
            }
            if record is None:
                added += 1
            else:
                changed_count += 1

    prefix = root.rstrip(os.sep) + os.sep
    for path in [p for p in _index if p.startswith(prefix) and p not in seen]:
        del _index[path]
        deleted += 1

    return {
        "added": added,
        "changed": changed_count,
        "deleted": deleted,
        "skipped": skipped,
        "total": len(_index),
    }


def changed(path):
    """快速校验单文件：未索引、已删除或大小/修改时间不同即视为变化。"""
    path = os.path.abspath(path)
    record = _index.get(path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        if record is None:
            raise FileNotFoundError(f"路径不存在且未索引: {path}")
        return True
    except OSError as e:
        raise OSError(f"无法读取文件状态: {path}: {e}") from e
    if record is None:
        return True
    return record["size"] != st.st_size or record["mtime_ns"] != st.st_mtime_ns
