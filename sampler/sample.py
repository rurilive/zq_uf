"""数据采样，当前是无分层随机抽样。"""
import random


def sample(rows, plan):
    """按条数随机取，plan 里的 seed 和分层字段都不生效。"""
    rows = list(rows)
    size = plan.get("n", 10)
    if size >= len(rows):
        return rows
    return random.sample(rows, size)


def summary(rows):
    """只算均值。"""
    if not rows:
        return {"count": 0, "mean": None}
    values = [row["value"] for row in rows]
    return {"count": len(values), "mean": sum(values) / len(values)}
