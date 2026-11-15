"""血缘影响，当前只有表级一跳。"""

_edges = {}


def impact(table, field=None):
    """只看直接下游表，字段忽略。"""
    direct = list(_edges.get(table, []))
    return {"table": table, "field": field, "downstream": direct, "depth": 1, "paths": [[table, t] for t in direct]}


def paths(node):
    """一跳路径。"""
    return {"node": node, "paths": [[node, t] for t in _edges.get(node, [])], "cycles": []}
