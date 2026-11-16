"""账号权限审计：比对授权范围与实际使用，标记异常账号并生成待收回清单。

account 是内存里的账号快照，常用字段：
- id/system/role/status：账号、所属系统、岗位、在职状态（active|left|transferred）
- permissions：授权范围
- used：每个权限距最近一次使用的天数，键缺失表示从未使用
- last_login：距最近一次登录的天数，用于长期未登录统计
- granted_day/now：授权日与当前审计日（逻辑时钟），用于区分新授权与长期未使用

audit(account) 和 report() 的调用方式保持不变，新增参数均为可选。
"""

from datetime import datetime, timezone

# 长期未使用权限阈值（天）：从未使用且授权满该天数，或最近使用距今达到该天数
STALE_DAYS = 30
# 长期未登录账号阈值（天）
INACTIVE_DAYS = 60

ACTIVE = "active"
LEFT = "left"
TRANSFERRED = "transferred"
_ABNORMAL_STATUS = {LEFT: "left", TRANSFERRED: "transferred"}

_accounts = {}
_changes = []
_seq = 0


def reset():
    """清空内存态，主要给测试和演示重复运行使用。"""
    _accounts.clear()
    _changes.clear()
    global _seq
    _seq = 0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _check(account):
    if not isinstance(account, dict):
        raise ValueError("account must be a dict")
    if "id" not in account:
        raise ValueError("account needs id")
    if not isinstance(account.get("permissions", []), list):
        raise ValueError("permissions must be a list")
    if not isinstance(account.get("used", {}), dict):
        raise ValueError("used must be a dict")
    last_login = account.get("last_login")
    if last_login is not None and not isinstance(last_login, (int, float)):
        raise ValueError("last_login must be days since last login")


def _permission_age(record):
    granted_day = record.get("granted_day")
    now = record.get("now")
    if granted_day is not None and now is not None:
        return now - granted_day
    return None


def _derive(record, stale_days, inactive_days):
    """根据账号原始快照计算 unused/stale/abnormal/reclaim 等派生结果。"""
    permissions = list(record["permissions"])
    used = record["used"]
    age = _permission_age(record)

    unused = [perm for perm in permissions if perm not in used]

    stale = []
    for perm in permissions:
        if perm in used:
            if used[perm] >= stale_days:
                stale.append(perm)
        elif age is not None:
            # 从未使用：有授权时间且授权已满阈值才算长期未使用；
            # 新授权或缺少授权时间的，只算 unused，不进入收回清单
            if age >= stale_days:
                stale.append(perm)

    status = record["status"]
    abnormal = []
    if status in _ABNORMAL_STATUS:
        abnormal.append({
            "account": record["id"],
            "system": record["system"],
            "role": record["role"],
            "status": status,
            "reason": _ABNORMAL_STATUS[status],
        })

    reclaim = []
    if status in _ABNORMAL_STATUS:
        # 离职/转岗账号：授权范围整体待收回
        for perm in permissions:
            reclaim.append(_reclaim_entry(record, perm, status))
    else:
        # 在职账号：只收长期未使用的权限
        for perm in stale:
            reclaim.append(_reclaim_entry(record, perm, "stale"))

    record["unused"] = unused
    record["stale"] = stale
    record["abnormal"] = abnormal
    record["reclaim"] = reclaim
    record["inactive"] = (
        record["last_login"] is not None
        and record["last_login"] >= inactive_days
    )


def _reclaim_entry(record, permission, reason):
    return {
        "account": record["id"],
        "system": record["system"],
        "role": record["role"],
        "permission": permission,
        "reason": reason,
        "last_used": record["used"].get(permission),
    }


def _result(record):
    return {
        "account": record["id"],
        "system": record["system"],
        "role": record["role"],
        "status": record["status"],
        "granted": list(record["permissions"]),
        "last_used": dict(record["used"]),
        "last_login": record["last_login"],
        "unused": list(record["unused"]),
        "stale": list(record["stale"]),
        "abnormal": [dict(item) for item in record["abnormal"]],
        "reclaim": [dict(item) for item in record["reclaim"]],
        "inactive": record["inactive"],
        "pending_recheck": record["dirty"],
    }


def audit(account, stale_days=STALE_DAYS, inactive_days=INACTIVE_DAYS):
    """登记账号快照并比对授权与实际使用，返回单账号审计结果。"""
    _check(account)
    account_id = account["id"]
    raw = {
        "id": account_id,
        "system": account.get("system", "default"),
        "role": account.get("role", "unknown"),
        "status": account.get("status", ACTIVE),
        "permissions": list(account.get("permissions", [])),
        "used": dict(account.get("used", {})),
        "last_login": account.get("last_login"),
        "granted_day": account.get("granted_day"),
        "now": account.get("now"),
    }

    previous = _accounts.get(account_id)
    if previous is None:
        raw["dirty"] = False
        raw["reviewed"] = False
    else:
        # 快照内容发生变化才需要重新复核，未变化的账号不打扰管理员
        changed = any(
            previous[key] != raw[key]
            for key in ("permissions", "used", "status", "role",
                        "system", "last_login")
        )
        raw["dirty"] = previous["dirty"] or changed
        raw["reviewed"] = previous["reviewed"] and not changed
    _accounts[account_id] = raw

    _derive(raw, stale_days, inactive_days)
    return _result(raw)


def _require(account_id):
    record = _accounts.get(account_id)
    if record is None:
        raise ValueError("unknown account")
    return record


def _log(account_id, action, permission, actor):
    global _seq
    _seq += 1
    entry = {
        "seq": _seq,
        "time": _now_iso(),
        "account": account_id,
        "action": action,
        "permission": permission,
        "actor": actor,
    }
    _changes.append(entry)
    return entry


def grant(account_id, permission, actor=None):
    """授权一个权限，变更记录保留变更人和时间。"""
    record = _require(account_id)
    if permission in record["permissions"]:
        raise ValueError("permission already granted")
    record["permissions"].append(permission)
    record["dirty"] = True
    record["reviewed"] = False
    _derive(record, STALE_DAYS, INACTIVE_DAYS)
    entry = _log(account_id, "grant", permission, actor)
    return {
        "account": account_id,
        "permission": permission,
        "actor": actor,
        "time": entry["time"],
        "seq": entry["seq"],
        "recorded": True,
    }


def _impact(record, removing):
    removing = list(removing)
    removed = set(removing)
    return {
        "account": record["id"],
        "system": record["system"],
        "role": record["role"],
        "status": record["status"],
        "accounts_affected": [record["id"]],
        "removing": removing,
        "permission_count": len(removing),
        "remaining": [p for p in record["permissions"] if p not in removed],
    }


def reclaim(account_id, permissions=None, actor=None, dry_run=False):
    """收回权限：默认收回该账号清单内的全部权限，dry_run 只给影响范围。"""
    record = _require(account_id)
    candidates = [item["permission"] for item in record["reclaim"]]
    if permissions is None:
        removing = candidates
    else:
        for perm in permissions:
            if perm not in record["permissions"]:
                raise ValueError(f"permission not granted: {perm}")
            if perm not in candidates:
                raise ValueError(
                    f"permission not in reclaim list: {perm}"
                )
        removing = list(permissions)

    impact = _impact(record, removing)
    if dry_run:
        return {"applied": False, "impact": impact}

    for perm in removing:
        record["permissions"].remove(perm)
        record["used"].pop(perm, None)
        _log(account_id, "reclaim", perm, actor)
    record["dirty"] = True
    record["reviewed"] = False
    _derive(record, STALE_DAYS, INACTIVE_DAYS)
    return {
        "applied": True,
        "impact": impact,
        "removed": removing,
        "audit": _result(record),
    }


def recheck(account_id):
    """收回后按账号复核一次：重算该账号审计结果并标记已复核。"""
    record = _require(account_id)
    if record["reviewed"]:
        return {
            "account": account_id,
            "reviewed": True,
            "already_reviewed": True,
            "audit": _result(record),
        }
    _derive(record, STALE_DAYS, INACTIVE_DAYS)
    record["reviewed"] = True
    record["dirty"] = False
    return {
        "account": account_id,
        "reviewed": True,
        "already_reviewed": False,
        "audit": _result(record),
    }


def changes(account_id=None):
    """权限变更记录，按时间（seq）排序，可按账号过滤。"""
    items = _changes if account_id is None else [
        item for item in _changes if item["account"] == account_id
    ]
    return [dict(item) for item in sorted(items, key=lambda item: item["seq"])]


def report(system=None):
    """汇总审计结果；传 system 时管理员只看该系统，清单按系统和岗位分组。"""
    records = list(_accounts.values())
    if system is not None:
        records = [item for item in records if item["system"] == system]

    systems = {}
    unused = 0
    stale = 0
    abnormal = []
    inactive = []
    grouped = {}
    reclaim_count = 0
    pending_recheck = []

    for record in records:
        systems[record["system"]] = systems.get(record["system"], 0) + 1
        unused += len(record["unused"])
        stale += len(record["stale"])
        abnormal.extend(dict(item) for item in record["abnormal"])
        if record["inactive"]:
            inactive.append({
                "account": record["id"],
                "system": record["system"],
                "last_login": record["last_login"],
            })
        if record["dirty"]:
            pending_recheck.append(record["id"])

        for item in record["reclaim"]:
            role_group = grouped.setdefault(record["system"], {}).setdefault(
                record["role"], []
            )
            role_group.append(dict(item))
            reclaim_count += 1

    return {
        "accounts": len(records),
        "systems": systems,
        "unused": unused,
        "stale": stale,
        "abnormal": abnormal,
        "inactive": {
            "threshold": INACTIVE_DAYS,
            "count": len(inactive),
            "accounts": inactive,
        },
        "reclaim": grouped,
        "reclaim_count": reclaim_count,
        "pending_recheck": pending_recheck,
    }
