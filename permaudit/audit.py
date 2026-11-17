"""账号权限审计：对比授权范围与实际使用，输出未使用权限、异常账号和待收回清单。

account 是内存里的账号快照，支持的字段：
- id/system/role/status：账号、所属系统、岗位、在职状态
  status 取值 active（在职）、left（离职）、transferred（转岗），缺省 active
- permissions：授权范围（权限名列表）
- used：{权限名: 最近一次使用距今天数}，不在 used 里的权限视为未使用
- last_login：最近登录距今天数；缺省时取 used 里的最小值，完全没有使用记录视为从未登录
- granted_day/now：授权日与当前审计日，用来判断“从未使用”的权限是否已长期闲置
- stale_days：单账号闲置阈值，缺省用模块常量 STALE_DAYS
- role_permissions：{岗位: [该岗位应有权限]}，转岗后不在现岗位范围内的权限按残留处理
"""

STALE_DAYS = 90
DORMANT_DAYS = 180

# 在职状态别名，兼容不同系统快照里的叫法，统一归一到三个内部状态。
_STATUS_ALIASES = {
    "active": "active",
    "left": "left",
    "departed": "left",
    "resigned": "left",
    "quit": "left",
    "transferred": "transferred",
    "moved": "transferred",
    "transfer": "transferred",
}

_accounts = {}
_changes = []
_reviews = []
_clock = 0
_seq = 0


def reset():
    """清空全部登记、变更和复核记录，主要给测试和新一轮审计使用。"""
    _accounts.clear()
    _changes.clear()
    _reviews.clear()
    global _clock, _seq
    _clock = 0
    _seq = 0


def _tick(time):
    """推进逻辑时钟：显式给时间就用该时间（调用方负责按时间顺序提供），
    否则单调 +1，保证没给时间的变更记录也能稳定排序。"""
    global _clock
    if time is None:
        _clock += 1
        return _clock
    _clock = time
    return time


def _next_seq():
    global _seq
    _seq += 1
    return _seq


def _check(account):
    if not isinstance(account, dict):
        raise ValueError("account must be a dict")
    if "id" not in account:
        raise ValueError("account needs id")
    if not isinstance(account.get("permissions", []), list):
        raise ValueError("permissions must be a list")
    if not isinstance(account.get("used", {}), dict):
        raise ValueError("used must be a dict")


def _normalize_status(raw):
    status = raw if raw is not None else "active"
    return _STATUS_ALIASES.get(status, status)


def _login_age(account, used):
    """最近登录距今天数：显式字段优先，其次用最近一次权限使用代替，都没有返回 None。"""
    if account.get("last_login") is not None:
        return account["last_login"]
    if account.get("last_login_day") is not None:
        return account["last_login_day"]
    if used:
        return min(used.values())
    return None


def _permission_age(perm, used, granted_day, now):
    """权限闲置天数：有使用记录取使用记录，否则用 授权日->当前日 推算，推不出返回 None。"""
    if perm in used:
        return used[perm]
    if granted_day is not None and now is not None:
        return now - granted_day
    return None


def _reclaim_entry(item_status, perm, age):
    return {
        "permission": perm,
        "reason": item_status if item_status in ("left", "transferred") else "stale",
        "last_used": age,
    }


def audit(account):
    """登记一个账号快照并返回该账号的审计结论。

    返回授权范围 granted、最近使用 last_used、未使用权限 unused、长期闲置 stale、
    异常账号 abnormal 和待收回清单 reclaim。
    """
    _check(account)
    account_id = account["id"]
    system = account.get("system", "default")
    role = account.get("role", "unknown")
    status = _normalize_status(account.get("status", "active"))
    permissions = list(account.get("permissions", []))
    used = dict(account.get("used", {}))
    now = account.get("now")
    granted_day = account.get("granted_day")
    stale_days = account.get("stale_days", STALE_DAYS)

    # 未使用：授权里有、使用记录里没有（保持成员判断语义）。
    unused = [perm for perm in permissions if perm not in used]

    # 长期未使用：能推算出闲置天数且达到阈值的权限，单独列出。
    stale = []
    ages = {}
    for perm in permissions:
        age = _permission_age(perm, used, granted_day, now)
        ages[perm] = age
        if age is not None and age >= stale_days:
            stale.append(perm)

    # 转岗账号靠岗位权限目录判断哪些是原岗位残留；没有目录时只收长期闲置部分。
    catalog = account.get("role_permissions") or {}
    scope = set(catalog.get(role, [])) if catalog else None

    abnormal = []
    if status in ("left", "transferred"):
        abnormal.append({
            "account": account_id,
            "system": system,
            "role": role,
            "status": status,
            "reason": status,
        })

    reclaim = []
    for perm in permissions:
        reason = None
        if status == "left":
            reason = "left"
        elif status == "transferred":
            if scope is not None and perm not in scope:
                reason = "transferred"
            elif perm in stale:
                reason = "stale"
        elif perm in stale:
            reason = "stale"
        if reason is not None:
            entry = _reclaim_entry(status, perm, ages[perm])
            entry["reason"] = reason
            entry.update({
                "account": account_id,
                "system": system,
                "role": role,
                "status": status,
            })
            reclaim.append(entry)

    previous = _accounts.get(account_id, {})
    _accounts[account_id] = {
        "system": system,
        "role": role,
        "status": status,
        "permissions": permissions,
        "used": used,
        "unused": unused,
        "stale": stale,
        "abnormal": abnormal,
        "reclaim": reclaim,
        "login_age": _login_age(account, used),
        # 复核状态跨重新审计保留，避免快照刷新把待复核标记冲掉。
        "changed": previous.get("changed", False),
        "reviewed_at": previous.get("reviewed_at"),
    }

    return {
        "account": account_id,
        "system": system,
        "role": role,
        "status": status,
        "granted": permissions,
        "last_used": used,
        "unused": unused,
        "stale": stale,
        "abnormal": abnormal,
        "reclaim": reclaim,
    }


def _record_change(action, account_id, permission, actor, time, item):
    record = {
        "seq": _next_seq(),
        "time": _tick(time),
        "action": action,
        "actor": actor,
        "account": account_id,
        "permission": permission,
        "system": item["system"],
        "role": item["role"],
    }
    _changes.append(record)
    return record


def grant(account_id, permission, actor="unknown", time=None):
    """登记一次授权，保留变更人和时间；前两个位置参数与旧调用方式兼容。"""
    item = _accounts.get(account_id)
    if item is None:
        raise ValueError("unknown account")
    if permission in item["permissions"]:
        raise ValueError("permission already granted: %s" % permission)
    item["permissions"].append(permission)
    item["changed"] = True
    record = _record_change("grant", account_id, permission, actor, time, item)
    return {
        "account": account_id,
        "permission": permission,
        "action": "grant",
        "actor": record["actor"],
        "time": record["time"],
        "recorded": True,
    }


def reclaim(account_id, permissions=None, actor="unknown", time=None):
    """收回权限：先给影响范围再执行移除，并逐条留痕。

    permissions 缺省表示收回该账号待收回清单上的全部权限。
    """
    item = _accounts.get(account_id)
    if item is None:
        raise ValueError("unknown account")
    if permissions is None:
        permissions = [entry["permission"] for entry in item["reclaim"]]
    permissions = list(permissions)

    missing = [perm for perm in permissions if perm not in item["permissions"]]
    if missing:
        raise ValueError("permission not held: %s" % ", ".join(missing))

    # 收回前给出影响范围：哪个系统、哪个岗位、受影响权限和数量。
    impact = {
        "account": account_id,
        "system": item["system"],
        "role": item["role"],
        "status": item["status"],
        "permissions": list(permissions),
        "affected": len(permissions),
    }

    records = []
    for perm in permissions:
        item["permissions"].remove(perm)
        item["used"].pop(perm, None)
        records.append(_record_change("reclaim", account_id, perm, actor, time, item))

    item["unused"] = [perm for perm in item["permissions"] if perm not in item["used"]]
    item["stale"] = [perm for perm in item["stale"] if perm in item["permissions"]]
    item["reclaim"] = [
        entry for entry in item["reclaim"] if entry["permission"] in item["permissions"]
    ]
    if permissions:
        item["changed"] = True

    return {
        "impact": impact,
        "removed": list(permissions),
        "affected": len(permissions),
        "recorded": records,
    }


def review(account_id, actor="unknown", time=None):
    """按账号复核一次：清掉该账号的待复核标记并留痕；没有待复核变化时拒绝重复复核。"""
    item = _accounts.get(account_id)
    if item is None:
        raise ValueError("unknown account")
    if not item.get("changed"):
        raise ValueError("no pending changes for account: %s" % account_id)
    record = {
        "seq": _next_seq(),
        "time": _tick(time),
        "action": "review",
        "actor": actor,
        "account": account_id,
        "system": item["system"],
        "role": item["role"],
    }
    _reviews.append(record)
    item["changed"] = False
    item["reviewed_at"] = record["time"]
    return record


def changes(account_id=None):
    """权限变更记录，按时间（再按序号）排序，便于复盘。"""
    records = _changes
    if account_id is not None:
        records = [record for record in records if record["account"] == account_id]
    return sorted(records, key=lambda record: (record["time"], record["seq"]))


def report():
    """汇总全部已登记账号：管理员按系统查看，附异常、休眠、待收回和待复核清单。"""
    systems = {}
    unused_total = 0
    abnormal = []
    reclaim_list = {}
    reclaim_total = 0
    dormant = []
    review_pending = []

    for account_id, item in _accounts.items():
        system = item["system"]
        systems[system] = systems.get(system, 0) + 1
        unused_total += len(item["unused"])
        abnormal.extend(item["abnormal"])

        # 待收回清单按系统、再按岗位分组。
        if item["reclaim"]:
            roles = reclaim_list.setdefault(system, {})
            roles.setdefault(item["role"], []).extend(item["reclaim"])
            reclaim_total += len(item["reclaim"])

        # 长期未登录账号单独统计；从未登录的 last_login 为 None，同样列出。
        login_age = item.get("login_age")
        if login_age is None or login_age >= DORMANT_DAYS:
            dormant.append({
                "account": account_id,
                "system": system,
                "role": item["role"],
                "last_login": login_age,
            })

        if item.get("changed"):
            review_pending.append(account_id)

    return {
        "accounts": len(_accounts),
        "systems": systems,
        "unused": unused_total,
        "reclaim": reclaim_total,
        "reclaim_list": reclaim_list,
        "abnormal": abnormal,
        "dormant": {"count": len(dormant), "accounts": dormant},
        "review": sorted(review_pending),
        "changes": changes(),
    }
