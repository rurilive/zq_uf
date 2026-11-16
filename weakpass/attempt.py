"""弱口令试错：跟着账号锁定状态走，支持断点续试。"""

import time

_DEFAULT_WORDS = ("123456", "password", "admin", "s3cret", "P@ssw0rd")

_ACCOUNTS = {}


def reset():
    """清空账号记录。"""
    _ACCOUNTS.clear()
    return {"accounts": 0}


def _state(user, system):
    state = _ACCOUNTS.get(user)
    if state is None:
        state = {
            "account": user,
            "system": system,
            "success": [],       # 成功口令，跨轮次保持
            "missed": [],        # 已试未命中的口令，跨轮次累积
            "tried": [],         # 全部试过的口令
            "pending": [],       # 待试队列，锁定恢复后从这里继续
            "locked_at": None,
            "locked_until": None,
            "skipped": [],       # {"word", "reason"}
        }
        _ACCOUNTS[user] = state
    elif system:
        state["system"] = system
    return state


def _enqueue(state, words, skipped):
    """新字典并入待试队列；已试过的和队列里已有的不再重复试。"""
    for word in words:
        if word in state["success"] or word in state["missed"]:
            skipped.append({"word": word, "reason": "already_tried"})
        elif word in state["pending"]:
            skipped.append({"word": word, "reason": "duplicate"})
        else:
            state["pending"].append(word)


def _round_record(state, tried, skipped):
    locked = []
    if state["locked_at"] is not None:
        locked.append({
            "account": state["account"],
            "locked_at": state["locked_at"],
            "locked_until": state["locked_until"],
        })
    return {
        "account": state["account"],
        "system": state["system"],
        "tried": tried,
        "success": list(state["success"]),
        "missed": list(state["missed"]),
        "locked": locked,
        "remaining": list(state["pending"]),
        "skipped": skipped,
    }


def attempt(account):
    """按账号状态试错：锁定即停并记录锁定时间，恢复后从停下的口令继续。

    account 字段：user（必填）、system、dictionary、valid、locked、
    locked_until、lock_after（连续失败多少次后锁定）、lock_duration、now。
    """
    if not isinstance(account, dict):
        raise ValueError("account must be a dict")
    user = account.get("user", "")
    if not user:
        raise ValueError("user is required")
    now = account.get("now", time.time())
    state = _state(user, account.get("system"))
    valid = account.get("valid")
    tried = []
    skipped = []

    # 已成功过的账号保持标记，换个字典也不再试、不会回退成未命中
    if state["success"]:
        for word in account.get("dictionary") or []:
            skipped.append({"word": word, "reason": "already_success"})
        state["skipped"].extend(skipped)
        return _round_record(state, tried, skipped)

    # 锁定判断：显式 locked，或锁定截止时间未到
    locked_until = account.get("locked_until", state["locked_until"])
    if account.get("locked") or (locked_until is not None and locked_until > now):
        if state["locked_at"] is None:
            state["locked_at"] = now
        state["locked_until"] = locked_until
        _enqueue(state, account.get("dictionary") or [], skipped)
        skipped.extend({"word": w, "reason": "locked"} for w in state["pending"])
        state["skipped"].extend(skipped)
        return _round_record(state, tried, skipped)

    # 未锁定（或已恢复）：清掉锁定标记，从停下的口令继续
    state["locked_at"] = None
    state["locked_until"] = None
    words = account.get("dictionary")
    if words is None and not state["tried"] and not state["pending"]:
        words = _DEFAULT_WORDS
    _enqueue(state, words or [], skipped)

    lock_after = account.get("lock_after")
    failures = 0
    while state["pending"]:
        word = state["pending"].pop(0)
        tried.append(word)
        state["tried"].append(word)
        if word == valid:
            state["success"].append(word)
            break
        state["missed"].append(word)
        failures += 1
        if lock_after is not None and failures >= lock_after:
            # 出现锁定：停止该账号，记录锁定时间，剩余口令留待恢复后继续
            state["locked_at"] = now
            duration = account.get("lock_duration")
            state["locked_until"] = now + duration if duration else None
            skipped.extend({"word": w, "reason": "locked"} for w in state["pending"])
            break

    state["skipped"].extend(skipped)
    return _round_record(state, tried, skipped)


def report(system=None):
    """汇总各账号进度，可按系统过滤；锁定账号不算通过，未命中口令可单独筛出。"""
    records = [s for s in _ACCOUNTS.values() if system is None or s["system"] == system]
    success = []
    missed = []
    locked = []
    skipped = []
    systems = {}
    for record in records:
        name = record["system"] or "default"
        bucket = systems.setdefault(name, {
            "accounts": 0, "attempts": 0, "passed": 0,
            "locked": 0, "remaining": 0, "missed": 0,
        })
        bucket["accounts"] += 1
        bucket["attempts"] += len(record["tried"])
        is_locked = record["locked_at"] is not None
        for word in record["success"]:
            success.append({"account": record["account"], "system": record["system"], "password": word})
        for word in record["missed"]:
            missed.append({"account": record["account"], "system": record["system"], "password": word})
        if record["success"] and not is_locked:
            bucket["passed"] += 1
        if is_locked:
            locked.append({
                "account": record["account"],
                "system": record["system"],
                "locked_at": record["locked_at"],
                "locked_until": record["locked_until"],
            })
            bucket["locked"] += 1
        bucket["remaining"] += len(record["pending"])
        bucket["missed"] += len(record["missed"])
        skipped.extend({"account": record["account"], **item} for item in record["skipped"])
    return {
        "accounts": len(records),
        "attempts": sum(len(r["tried"]) for r in records),
        "passed": sum(1 for r in records if r["success"] and r["locked_at"] is None),
        "success": success,
        "locked": locked,
        "remaining": sum(len(r["pending"]) for r in records),
        "missed": missed,
        "skipped": skipped,
        "systems": systems,
    }
