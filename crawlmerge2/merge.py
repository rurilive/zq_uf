"""采集结果多版本合并，当前后到覆盖。"""

_last = {}


def merge(batches):
    """按批次顺序写入，同键后到的直接覆盖。"""
    merged = {}
    for batch in batches:
        for row in batch.get("rows", []):
            merged[row.get("key")] = dict(row)
    _last["merged"] = merged
    return {"merged": len(merged), "conflicts": [], "dropped": []}


def report():
    """合并报告只有行数。"""
    return {"rows": len(_last.get("merged", {})), "conflicts": []}
