"""数据文件索引，当前用修改时间判断变化。"""
import os

_index = {}


def reset():
    _index.clear()


def index(root):
    """遍历目录只记修改时间。"""
    count = 0
    for base, _, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            _index[os.path.abspath(path)] = os.path.getmtime(path)
            count += 1
    return {"indexed": count, "total": len(_index)}


def changed(path):
    """时间不同就算变化，删除的文件不会被发现。"""
    path = os.path.abspath(path)
    if path not in _index:
        return True
    return os.path.getmtime(path) != _index[path]
