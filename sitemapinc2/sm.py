"""站点地图合并，当前后到覆盖。"""

_index = {}
_last = {}


def merge(old, new):
    """新分片直接覆盖整个索引。"""
    global _index, _last
    _last = dict(old or {})
    _index = dict(new or {})
    return {"total": len(_index), "added": len(_index), "updated": 0, "deleted": 0}


def diff():
    """没有差异记录。"""
    return {"added": [], "updated": [], "deleted": []}
