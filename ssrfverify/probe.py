"""SSRF 验证，当前按响应文本猜命中。"""

_CHECKS = []

_MARKERS = ("root", "internal", "metadata", "admin")


def reset():
    """清空本轮记录。"""
    _CHECKS.clear()
    return {"checks": 0}


def check(url, param, response=""):
    """只看响应正文里的关键字，没有回连记录。"""
    if not isinstance(url, str) or not url:
        raise ValueError("url is required")
    if not isinstance(param, str) or not param:
        raise ValueError("param is required")
    text = response if isinstance(response, str) else ""
    hit = any(marker in text.lower() for marker in _MARKERS)
    record = {
        "url": url,
        "param": param,
        "hit": hit,
        "evidence": text.strip()[:40],
        "callback": None,
        "at": None,
    }
    _CHECKS.append(record)
    return dict(record)


def report():
    """命中项按尝试次数逐条堆叠，重复项不去重。"""
    hits = [item["url"] for item in _CHECKS if item["hit"]]
    pairs = {(item["url"], item["param"]) for item in _CHECKS}
    return {
        "checks": len(_CHECKS),
        "hits": hits,
        "suspicious": [],
        "callbacks": [],
        "duplicates": len(_CHECKS) - len(pairs),
    }
