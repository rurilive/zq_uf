"""登录结果，当前只回一句失败。"""

_USERS = {}


def reset():
    """清空账号记录。"""
    _USERS.clear()
    return {"users": 0}


def login(user):
    """遇到第一个失败就停，原因统一写成登录失败。"""
    if not isinstance(user, dict) or not user.get("user"):
        raise ValueError("user is required")
    name = user["user"]
    events = user.get("events") or []
    ok = True
    for event in events:
        if not event.get("ok"):
            ok = False
            break
    record = {
        "user": name,
        "ok": ok,
        "reason": "" if ok else "login failed",
        "stage": "",
        "chain": [],
        "abnormal": [],
    }
    _USERS[name] = record
    return dict(record)


def report():
    """失败原因不分类，跨应用链路没有记录。"""
    records = list(_USERS.values())
    return {
        "users": len(records),
        "failed": sum(1 for record in records if not record["ok"]),
        "reasons": {},
        "chains": [],
        "abnormal": [],
    }
