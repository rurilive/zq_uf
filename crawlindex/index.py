"""采集索引，当前重建会清空旧索引。"""

_index = {}


def rebuild():
    """先清空再逐条写入，期间查询会读到空索引。"""
    global _index
    _index = {}
    return {"rebuilt": 0, "switched": True, "checked": False}


def query(key):
    """按精确键查索引。"""
    return {"key": key, "found": key in _index, "value": _index.get(key)}
