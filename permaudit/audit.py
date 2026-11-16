"""账号权限审计，当前逐个账号比对授权和已用权限。

account 是内存里的账号快照，used 记录每个权限最近一次使用的天数。
"""

_accounts = {}


def _check(account):
    if not isinstance(account, dict):
        raise ValueError("account must be a dict")
    if "id" not in account:
        raise ValueError("account needs id")
    if not isinstance(account.get("permissions", []), list):
        raise ValueError("permissions must be a list")


def audit(account):
    """授权里有、used 里没有就算未使用，不看岗位和在职状态。"""
    _check(account)
    permissions = list(account.get("permissions", []))
    used = account.get("used", {})
    unused = [perm for perm in permissions if perm not in used]
    _accounts[account["id"]] = {
        "system": account.get("system", "default"),
        "role": account.get("role", "unknown"),
        "status": account.get("status", "active"),
        "permissions": permissions,
        "unused": unused,
    }
    return {
        "account": account["id"],
        "granted": permissions,
        "unused": unused,
        "abnormal": [],
        "reclaim": [],
    }


def grant(account_id, permission):
    """只记权限名，不记变更人和时间。"""
    item = _accounts.get(account_id)
    if item is None:
        raise ValueError("unknown account")
    item["permissions"].append(permission)
    return {
        "account": account_id,
        "permission": permission,
        "recorded": False,
    }


def report():
    """按系统数账号和未使用权限，不分岗位。"""
    systems = {}
    unused = 0
    for item in _accounts.values():
        system = item["system"]
        systems[system] = systems.get(system, 0) + 1
        unused += len(item["unused"])
    return {
        "accounts": len(_accounts),
        "systems": systems,
        "unused": unused,
        "reclaim": 0,
    }
