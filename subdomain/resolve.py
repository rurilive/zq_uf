"""按解析结果收敛子域枚举候选。"""

_ENTRIES = {}    # domain -> {ip: [name, ...]} 已确认有效入口，同地址名字合并
_WILDCARD = {}   # domain -> {name: 原始记录} 通配命中，保留原始解析记录
_INVALID = {}    # domain -> {name: 原始记录} 已确认无效（fail / timeout）
_ROUNDS = {}     # domain -> [每轮汇总]，按域名和轮次分开存放


def load(domain, rows):
    """登记一轮枚举拿到的候选，按解析结果分类收敛。"""
    if not isinstance(domain, str) or not domain:
        raise ValueError("domain 不能为空")
    if not isinstance(rows, list):
        raise ValueError("rows 必须是列表")
    entries = _ENTRIES.setdefault(domain, {})
    wildcard = _WILDCARD.setdefault(domain, {})
    invalid = _INVALID.setdefault(domain, {})
    rounds = _ROUNDS.setdefault(domain, [])

    skipped = 0
    added = {}
    round_wildcard = 0
    round_failed = 0
    round_timeout = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("候选必须是字典")
        name = row.get("name")
        if not name:
            raise ValueError("候选缺少名字")
        if name in invalid:
            skipped += 1  # 已确认无效，重新枚举时跳过
            continue
        record = {"name": name, "ip": row.get("ip", ""), "status": row.get("status", "ok")}
        status = record["status"]
        if status == "wildcard":
            wildcard.setdefault(name, record)  # 保留原始解析记录，便于事后确认误判
            round_wildcard += 1
        elif status == "timeout":
            invalid[name] = record
            round_timeout += 1
        elif status != "ok" or not record["ip"]:
            invalid[name] = record
            round_failed += 1
        else:
            names = entries.setdefault(record["ip"], [])
            if name not in names:
                names.append(name)
                added.setdefault(record["ip"], []).append(name)
    rounds.append({
        "round": len(rounds) + 1,
        "candidates": len(rows),
        "skipped": skipped,
        "added": added,
        "wildcard": round_wildcard,
        "failed": round_failed,
        "timeout": round_timeout,
    })
    return {
        "domain": domain,
        "loaded": len(rows) - skipped,
        "skipped": skipped,
        "round": len(rounds),
    }


def enumerate(domain):
    """返回收敛后的有效入口、别名、通配命中和无效数量。"""
    if domain not in _ROUNDS:
        raise ValueError("域名未载入")
    entries = _ENTRIES[domain]
    names = sum(len(v) for v in entries.values())
    return {
        "domain": domain,
        "entries": [{"ip": ip, "names": list(v)} for ip, v in entries.items()],
        "total": len(entries),
        "aliases": names - len(entries),
        "wildcard": len(_WILDCARD[domain]),
        "invalid": len(_INVALID[domain]),
        "round": len(_ROUNDS[domain]),
    }


def report(domain):
    """按域名上报真实覆盖：有效入口数而非候选条数。"""
    out = enumerate(domain)
    invalid = _INVALID[domain].values()
    rounds = _ROUNDS[domain]
    return {
        "domain": domain,
        "total": sum(r["candidates"] for r in rounds),
        "valid": out["total"],
        "aliases": out["aliases"],
        "wildcard": out["wildcard"],
        "failed": sum(1 for r in invalid if r["status"] != "timeout"),
        "timeout": sum(1 for r in invalid if r["status"] == "timeout"),
        "new": sum(len(v) for v in rounds[-1]["added"].values()),
        "round": out["round"],
    }


def diff(domain):
    """最近一轮相对之前所有轮次新增的入口，避免重复上报已确认记录。"""
    if domain not in _ROUNDS:
        raise ValueError("域名未载入")
    last = _ROUNDS[domain][-1]
    return {
        "domain": domain,
        "round": last["round"],
        "entries": [{"ip": ip, "names": list(v)} for ip, v in last["added"].items()],
        "total": len(last["added"]),
    }


def wildcards(domain):
    """通配命中名字的原始解析记录，用于事后确认是否误判。"""
    if domain not in _ROUNDS:
        raise ValueError("域名未载入")
    return [dict(r) for r in _WILDCARD[domain].values()]


def restore(domain, name):
    """把误判的记录（通配命中或无效）单独恢复为有效入口。"""
    record = _WILDCARD.get(domain, {}).get(name)
    store = _WILDCARD
    if record is None:
        record = _INVALID.get(domain, {}).get(name)
        store = _INVALID
    if record is None:
        raise ValueError("记录不存在或无需恢复")
    if not record.get("ip"):
        raise ValueError("记录缺少地址，无法恢复")
    del store[domain][name]
    names = _ENTRIES[domain].setdefault(record["ip"], [])
    if name not in names:
        names.append(name)
    return {"domain": domain, "restored": name, "ip": record["ip"]}
