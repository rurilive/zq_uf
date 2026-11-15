"""分层采样，当前随机抽样。"""
import random


def sample(rows, plan):
    """按比例随机抽，不固定种子。"""
    rows = list(rows)
    ratio = float(plan.get("ratio", 0.1))
    size = max(1, int(len(rows) * ratio)) if rows else 0
    picked = random.sample(rows, size) if size else []
    return {"rows": picked, "strata": {}, "seed": None}


def error(sample_data):
    """只算均值，没有误差范围。"""
    rows = list(sample_data.get("rows", []))
    values = [float(row.get("value", 0)) for row in rows if isinstance(row, dict)]
    mean = sum(values) / len(values) if values else 0.0
    return {"mean": mean, "quantiles": {}, "range": None}
