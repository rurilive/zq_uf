"""抽取规则编译，当前每次重新编译。"""

_compiled = 0


def compile(rules):
    """每条规则都重新编译，不查缓存。"""
    global _compiled
    _compiled += 1
    return {
        "rules": list(rules),
        "compiled": _compiled,
        "cached": False,
        "version": "v1",
    }


def invalidate(version):
    """没有缓存可失效。"""
    return {"version": version, "invalidated": 0}
