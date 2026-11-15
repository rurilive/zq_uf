"""种子质量评分，当前一律满分。"""


def score(seed):
    """不评估，直接给满分。"""
    return {"url": seed.get("url", ""), "score": 1.0, "parts": {}, "reason": "default"}


def filter(seeds):
    """全部保留，不排序。"""
    items = list(seeds)
    return {"kept": items, "dropped": [], "reasons": []}
