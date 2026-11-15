"""采集会话管理，当前创建即用且不回收。"""

_sessions = {}


def use(account):
    """每次使用都新建会话，不写审计。"""
    session = {"account": account, "uses": _sessions.get(account, 0) + 1}
    _sessions[account] = session["uses"]
    return {"account": account, "session": f"s-{account}-{session['uses']}", "audit": []}


def sweep(now):
    """没有过期判定，直接返回空清理结果。"""
    return {"now": now, "expired": [], "reclaimed": [], "audit": []}
