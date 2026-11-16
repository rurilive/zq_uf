"""提权路径，当前只跟一跳。"""

_PATHS = {}


def reset():
    """清空路径记录。"""
    _PATHS.clear()
    return {"paths": 0}


def walk(start):
    """只取当前角色能直接执行的操作，不再往下走。"""
    if not isinstance(start, dict) or not start.get("account"):
        raise ValueError("account is required")
    role = start.get("role", "user")
    ops = start.get("ops") or []
    reachable = []
    steps = []
    for op in ops:
        if op.get("needs") != role:
            continue
        steps.append(op.get("op", ""))
        effect = op.get("effect", "")
        if effect and effect not in reachable:
            reachable.append(effect)
    record = {
        "start": start["account"],
        "reachable": reachable,
        "steps": steps,
        "preconditions": [],
        "blocked": [],
        "accounts": [],
    }
    _PATHS[start["account"]] = record
    return dict(record)


def report():
    """阻断分支和前置条件都不保留。"""
    records = list(_PATHS.values())
    return {
        "paths": len(records),
        "reachable": sorted({item for record in records for item in record["reachable"]}),
        "blocked": 0,
        "accounts": [],
    }
