"""攻击面清单：对外入口按 域名 + 地址 + 端口 合并，带状态、来源与确认时间。

同一入口只保留一条记录：重复登记合并来源、刷新确认时间，不新增行；
下线入口标记为 ``offline`` 并保留 ``offline_at``，之后重新登记即可恢复；
归属无法确认的入口留在待确认里；长期未确认的入口单独列出；
新增、下线、恢复和归属冲突都记入变化日志，可按月导出对比。
"""

from datetime import datetime, timedelta, timezone

# 超过多少天没有确认，就列为“长期未确认”。
STALE_DAYS = 90

_UNKNOWN_OWNER = "unknown"

# 入口键 (domain, address, port) -> 入口记录；插入顺序即发现顺序。
_ROWS = {}
# 清单变化事件，供 changes() 按月导出。
_EVENTS = []

_CHANGE_KINDS = ("added", "offline", "restored", "owner_conflict")


def reset():
    """清空已登记的入口和变化记录。"""
    _ROWS.clear()
    _EVENTS.clear()


def _time(value=None):
    """把 None、datetime 或 ISO-8601 字符串统一成带时区的 UTC 时间。"""
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("time must be a datetime or ISO-8601 string") from exc
    else:
        raise ValueError("time must be a datetime or ISO-8601 string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def _stamp(value=None):
    """返回统一格式的时间字符串。"""
    return _time(value).isoformat()


def _entry_key(entry):
    """取出入口的合并键，接受入口字典或 (domain, address, port)。"""
    if isinstance(entry, dict):
        domain = entry.get("domain")
        address = entry.get("address")
        port = entry.get("port", 443)
    elif isinstance(entry, (tuple, list)) and len(entry) == 3:
        domain, address, port = entry
    else:
        raise ValueError("entry must be a dict or (domain, address, port)")
    domain = str(domain or "").strip().lower()
    address = str(address or "").strip()
    if not domain or not address:
        raise ValueError("entry needs domain and address")
    if port is None:
        port = 443
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("entry port must be an integer") from exc
    return (domain, address, port)


def _read_entry(entry):
    """校验并归一化一条登记。"""
    if not isinstance(entry, dict):
        raise ValueError("entry must be a dict")
    key = _entry_key(entry)
    source = str(entry.get("source") or "manual").strip() or "manual"
    owner = str(entry.get("owner") or "").strip() or _UNKNOWN_OWNER
    status = str(entry.get("status") or "").strip().lower()
    discovered_at = entry.get("discovered_at")
    return {
        "domain": key[0],
        "address": key[1],
        "port": key[2],
        "owner": owner,
        "source": source,
        "offline": bool(entry.get("offline")) or status == "offline",
        "third_party": bool(entry.get("third_party")),
        "discovered_at": _stamp(discovered_at) if discovered_at else None,
    }


def _view(rec):
    """返回入口记录的副本，调用方改不到内部状态。"""
    out = dict(rec)
    out["sources"] = list(rec["sources"])
    out["source_seen"] = dict(rec["source_seen"])
    out["owner_candidates"] = list(rec["owner_candidates"])
    return out


def _log(kind, rec, at):
    """记录一次清单变化，保留当时的入口快照。"""
    _EVENTS.append({
        "at": at,
        "month": at[:7],
        "event": kind,
        "key": (rec["domain"], rec["address"], rec["port"]),
        "entry": _view(rec),
    })


def _summary(rec):
    """入口的简要视图，surface() 用它列出各类入口。"""
    summary = {
        "domain": rec["domain"],
        "address": rec["address"],
        "port": rec["port"],
        "owner": rec["owner"],
        "status": rec["status"],
        "sources": list(rec["sources"]),
        "discovered_at": rec["discovered_at"],
    }
    if rec["pending"]:
        summary["pending_reason"] = rec["pending_reason"]
        summary["owner_candidates"] = list(rec["owner_candidates"])
    return summary


def _new_record(item, at):
    """新建一条合并后的入口记录。"""
    offline = item["offline"]
    pending = item["owner"] == _UNKNOWN_OWNER
    return {
        "domain": item["domain"],
        "address": item["address"],
        "port": item["port"],
        "owner": item["owner"],
        "status": "offline" if offline else "active",
        "sources": [item["source"]],
        "source_seen": {item["source"]: at},
        "discovered_at": item["discovered_at"] or at,
        "last_seen_at": at,
        "confirmed_at": at,
        "offline_at": at if offline else None,
        "last_offline_at": at if offline else None,
        "restored_at": None,
        "third_party": item["third_party"],
        "pending": pending,
        "pending_reason": "owner_unknown" if pending else None,
        "owner_candidates": [],
    }


def _merge(rec, item, at):
    """把一次重复登记合并进已有入口。"""
    rec["last_seen_at"] = at
    rec["confirmed_at"] = at

    source = item["source"]
    if source not in rec["sources"]:
        rec["sources"].append(source)
    if source not in rec["source_seen"]:
        rec["source_seen"][source] = at

    if item["third_party"]:
        rec["third_party"] = True

    discovered = item["discovered_at"]
    if discovered and _time(discovered) < _time(rec["discovered_at"]):
        rec["discovered_at"] = discovered

    owner = item["owner"]
    if owner != _UNKNOWN_OWNER:
        if rec["owner"] == _UNKNOWN_OWNER:
            # 之前归属不明，这次补上了归属。
            rec["owner"] = owner
            rec["pending"] = False
            rec["pending_reason"] = None
            rec["owner_candidates"] = []
        elif owner != rec["owner"]:
            # 同一入口被记到两个业务名下，归属需要人工确认。
            was_conflict = rec["pending_reason"] == "owner_conflict"
            if owner not in rec["owner_candidates"]:
                rec["owner_candidates"].append(owner)
            rec["pending"] = True
            rec["pending_reason"] = "owner_conflict"
            if not was_conflict:
                _log("owner_conflict", rec, at)

    if item["offline"]:
        if rec["status"] == "active":
            rec["status"] = "offline"
            rec["offline_at"] = at
            rec["last_offline_at"] = at
            _log("offline", rec, at)
    elif rec["status"] == "offline":
        # 失效入口重新出现，恢复为有效入口，但保留上次下线时间。
        rec["status"] = "active"
        rec["restored_at"] = at
        rec["offline_at"] = None
        _log("restored", rec, at)


def _source_counts(rows):
    """按登记来源统计入口数量，一条入口可以同时来自多个来源。"""
    counts = {}
    for rec in rows:
        for source in rec["sources"]:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _status_counts(rows):
    counts = {"active": 0, "offline": 0, "pending": 0, "third_party": 0}
    for rec in rows:
        counts[rec["status"]] += 1
        if rec["pending"]:
            counts["pending"] += 1
        if rec["third_party"]:
            counts["third_party"] += 1
    return counts


def _matches_owner(rec, owner):
    """owner 筛选同时认主归属和归属冲突里的候选业务。"""
    if owner is None:
        return True
    return rec["owner"] == owner or owner in rec["owner_candidates"]


def surface(entries, now=None):
    """登记一批入口，按 域名 + 地址 + 端口 合并。

    entry 支持 domain、address、port（默认 443）、owner（默认 unknown）、
    source（默认 manual）、offline、third_party 和 discovered_at。
    同一入口重复登记只更新来源与确认时间；offline=True 标记失效但不删除，
    之后不带 offline 再登记即恢复为有效入口。
    """
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    at = _stamp(now)
    added = 0
    updated = 0
    for entry in entries:
        item = _read_entry(entry)
        key = (item["domain"], item["address"], item["port"])
        rec = _ROWS.get(key)
        if rec is None:
            rec = _new_record(item, at)
            _ROWS[key] = rec
            _log("added", rec, at)
            added += 1
        else:
            _merge(rec, item, at)
            updated += 1

    rows = list(_ROWS.values())
    counts = _status_counts(rows)
    summaries = [_summary(rec) for rec in rows]
    return {
        "added": added,
        "updated": updated,
        "total": len(summaries),
        "active": counts["active"],
        "offline": counts["offline"],
        "pending": counts["pending"],
        "third_party": counts["third_party"],
        "sources": _source_counts(rows),
        "active_entries": [row for row in summaries if row["status"] == "active"],
        "offline_entries": [row for row in summaries if row["status"] == "offline"],
        "pending_entries": [_summary(rec) for rec in _ROWS.values() if rec["pending"]],
    }


def confirm(entries=None, now=None):
    """重新确认入口仍在清单里，刷新 confirmed_at。

    entries 为 None 时确认全部入口；否则接受入口字典或三元组，
    未登记过的键放进 missing，不报错。
    """
    at = _stamp(now)
    if entries is None:
        for rec in _ROWS.values():
            rec["confirmed_at"] = at
            rec["last_seen_at"] = at
        return {"confirmed": len(_ROWS), "missing": []}
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")

    confirmed = 0
    missing = []
    for entry in entries:
        key = _entry_key(entry)
        rec = _ROWS.get(key)
        if rec is None:
            missing.append({"domain": key[0], "address": key[1], "port": key[2]})
            continue
        rec["confirmed_at"] = at
        rec["last_seen_at"] = at
        confirmed += 1
    return {"confirmed": confirmed, "missing": missing}


def stale(days=None, now=None, owner=None):
    """列出超过 days 天没有确认的入口，默认 STALE_DAYS 天。"""
    days = STALE_DAYS if days is None else int(days)
    limit = timedelta(days=days)
    now_dt = _time(now)
    rows = []
    for rec in _ROWS.values():
        if not _matches_owner(rec, owner):
            continue
        if now_dt - _time(rec["confirmed_at"]) >= limit:
            rows.append(_view(rec))
    return rows


def _empty_bucket(month):
    bucket = {"month": month}
    for kind in _CHANGE_KINDS:
        bucket[kind] = []
    bucket["counts"] = {kind: 0 for kind in _CHANGE_KINDS}
    return bucket


def changes(month=None, owner=None):
    """按月份导出清单变化：新增、下线、恢复和归属冲突。

    month 形如 "2026-11"；不传时返回所有月份的对比，桶内是变化时的
    入口快照，便于单独导出。owner 按业务归属筛选。
    """
    buckets = {}
    for event in _EVENTS:
        if not _matches_owner(event["entry"], owner):
            continue
        if month is not None and event["month"] != month:
            continue
        bucket = buckets.setdefault(event["month"], _empty_bucket(event["month"]))
        bucket[event["event"]].append(_view(event["entry"]))

    result = {}
    for name in sorted(buckets):
        bucket = buckets[name]
        bucket["counts"] = {kind: len(bucket[kind]) for kind in _CHANGE_KINDS}
        result[name] = bucket
    if month is not None:
        return result.get(month) or _empty_bucket(month)
    return result


def report(owner=None, status=None, now=None, stale_days=STALE_DAYS):
    """返回攻击面清单：有效、失效、待确认入口，来源分布和业务分组。

    owner 按业务归属筛选，status 取 active/offline；无法确认归属的入口
    只出现在待确认里，不计入 by_owner。changes 是当月的变化。
    """
    now_dt = _time(now)
    limit = timedelta(days=int(stale_days))

    rows = []
    for rec in _ROWS.values():
        if not _matches_owner(rec, owner):
            continue
        if status is not None and rec["status"] != status:
            continue
        rows.append(_view(rec))

    active_entries = [row for row in rows if row["status"] == "active"]
    offline_entries = [row for row in rows if row["status"] == "offline"]
    pending_entries = [row for row in rows if row["pending"]]
    third_party_entries = [row for row in rows if row["third_party"]]
    stale_entries = [
        row for row in rows if now_dt - _time(row["confirmed_at"]) >= limit
    ]

    groups = {}
    for row in active_entries:
        if row["owner"] == _UNKNOWN_OWNER:
            continue
        groups.setdefault(row["owner"], []).append(row)

    month = now_dt.strftime("%Y-%m")
    return {
        "as_of": now_dt.isoformat(),
        "total": len(rows),
        "active": len(active_entries),
        "offline": len(offline_entries),
        "pending": len(pending_entries),
        "third_party": len(third_party_entries),
        "stale": len(stale_entries),
        "by_owner": {name: len(items) for name, items in sorted(groups.items())},
        "groups": {name: items for name, items in sorted(groups.items())},
        "sources": _source_counts(rows),
        "entries": rows,
        "active_entries": active_entries,
        "offline_entries": offline_entries,
        "pending_entries": pending_entries,
        "third_party_entries": third_party_entries,
        "stale_entries": stale_entries,
        "changes": changes(month=month, owner=owner),
    }
