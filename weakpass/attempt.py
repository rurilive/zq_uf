"""弱口令试错，当前一次跑完整本字典。"""

_DEFAULT_WORDS = ("123456", "password", "admin", "s3cret", "P@ssw0rd")

_ACCOUNTS = {}


def reset():
    """清空账号记录。"""
    _ACCOUNTS.clear()
    return {"accounts": 0}


def attempt(account):
    """不读锁定状态，按字典顺序把所有口令试完。"""
    if not isinstance(account, dict):
        raise ValueError("account must be a dict")
    user = account.get("user", "")
    if not user:
        raise ValueError("user is required")
    words = list(account.get("dictionary") or _DEFAULT_WORDS)
    valid = account.get("valid")
    tried = []
    success = []
    for word in words:
        tried.append(word)
        if word == valid:
            success.append(word)
    record = {
        "account": user,
        "tried": tried,
        "success": success,
        "locked": [],
        "remaining": [],
        "skipped": [],
    }
    _ACCOUNTS[user] = record
    return dict(record)


def report():
    """成功项展开成账号加口令，锁定账号不单独统计。"""
    records = list(_ACCOUNTS.values())
    success = []
    for record in records:
        for word in record["success"]:
            success.append({"account": record["account"], "password": word})
    return {
        "accounts": len(records),
        "attempts": sum(len(record["tried"]) for record in records),
        "success": success,
        "locked": [],
        "remaining": 0,
        "skipped": [],
    }
