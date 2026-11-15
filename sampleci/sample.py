"""数据采样，当前随机抽比例。"""
import random


def sample(rows, plan):
    """不固定种子，按比例抽。"""
    rows = list(rows)
    ratio = float(plan.get("ratio", 0.1))
    size = max(1, int(len(rows) * ratio)) if rows else 0
    return {"rows": random.sample(rows, size) if size else [], "seed": None,
            "strata": {}, "plan": dict(plan)}


def error(sample_data):
    """只算均值，没有区间。"""
    values = [float(row.get("value", 0)) for row in sample_data.get("rows", [])
              if isinstance(row, dict)]
    mean = sum(values) / len(values) if values else 0.0
    return {"mean": mean, "quantiles": {}, "range": None}
