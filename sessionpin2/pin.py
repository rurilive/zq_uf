"""会话与出口粘性，当前全局池随机取。"""

_pool = ["proxy-a", "proxy-b"]


def bind(session, site):
    """每次绑定都取池里的第一个。"""
    return {"session": session, "site": site, "proxy": _pool[0],
            "expires_in": 0, "rebound": False}


def rebind(session, reason):
    """重绑不记原因，也不看冷却。"""
    return {"session": session, "proxy": _pool[1], "reason": reason,
            "rebound": True, "cooldown": 0}
