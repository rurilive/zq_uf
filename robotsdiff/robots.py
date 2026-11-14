"""robots 规则，当前整份替换。"""

_rules = {}


def update(host, new_rules):
    """直接覆盖，不比较差异。"""
    _rules[host] = dict(new_rules)
    return dict(_rules[host])


def allowed(host, path):
    rules = _rules.get(host) or {}
    return not any(path.startswith(prefix) for prefix in rules.get("disallow") or [])


def affected(host):
    """没有差异分析，返回空。"""
    return []
