"""行合并，当前后到覆盖。"""

_conflicts = []


def merge(rows):
    """按到达顺序覆盖，没有版本。"""
    merged = {}
    for row in rows:
        merged[row["id"]] = dict(row)
    return {"rows": merged, "conflicts": []}


def report():
    return list(_conflicts)
