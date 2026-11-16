"""WAF规则回归，当前只看攻击用例有没有被拦。"""

_CASES = [
    {"id": "c1", "biz": "search", "request": "/search?q=union select 1", "expect": "block"},
    {"id": "c2", "biz": "search", "request": "/search?q=laptop", "expect": "pass"},
    {"id": "c3", "biz": "login", "request": "/login?user=admin'--", "expect": "block"},
    {"id": "c4", "biz": "login", "request": "/login?user=alice", "expect": "pass"},
    {"id": "c5", "biz": "pay", "request": "/pay?note=<script>alert(1)</script>", "expect": "block"},
    {"id": "c6", "biz": "pay", "request": "/pay?note=thanks", "expect": "pass"},
]

_RESULT = {}


def reset():
    """清空上一轮回归结果。"""
    _RESULT.clear()


def regress(rules):
    """按规则逐条跑用例，只记录命中和攻击侧漏拦。"""
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    blocked = []
    missed = []
    for case in _CASES:
        hit = ""
        for rule in rules:
            if rule["pattern"] in case["request"]:
                hit = rule["id"]
                break
        if hit:
            blocked.append({"case": case["id"], "rule": hit})
        elif case["expect"] == "block":
            missed.append({"case": case["id"], "request": case["request"]})
    _RESULT.clear()
    _RESULT.update({"rules": len(rules), "blocked": blocked, "missed": missed})
    return {
        "rules": len(rules),
        "cases": len(_CASES),
        "blocked": len(blocked),
        "missed": len(missed),
    }


def report():
    """给出上一轮的命中、漏拦和用例总数。"""
    return {
        "rules": _RESULT.get("rules", 0),
        "cases": len(_CASES),
        "blocked": list(_RESULT.get("blocked", [])),
        "missed": list(_RESULT.get("missed", [])),
    }
