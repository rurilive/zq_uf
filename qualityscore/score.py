"""质量评分，当前等权。"""

_history = {}


def score(batch):
    """每条规则一分，按失败数扣。"""
    rules = batch.get("rules") or []
    failed = sum(1 for rule in rules if rule.get("failed"))
    total = max(1, len(rules))
    value = round((total - failed) / total, 2)
    _history.setdefault(batch.get("dataset", "default"), []).append(value)
    return {"score": value, "failed": failed, "total": total}


def trend(dataset):
    """只返回历史分数。"""
    return list(_history.get(dataset) or [])
