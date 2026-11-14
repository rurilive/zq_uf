"""站点地图合并，当前全量覆盖。"""

_index = {}


def merge(old, new):
    """新表直接替换旧表，删除不传播。"""
    global _index
    _index = dict(old)
    _index.update(new)
    return dict(_index)


def diff():
    """只返回当前键，不比较历史。"""
    return {"added": [], "removed": [], "updated": []}
