"""血缘影响面，当前一跳。"""

_edges = {}


def impact(table, field=None):
    """field 参数被忽略，只看直接下游。"""
    return sorted(_edges.get(table, []))


def paths(node):
    """没有多跳路径。"""
    return [node]


def record(src, dst):
    _edges.setdefault(src, []).append(dst)
    return len(_edges[src])
