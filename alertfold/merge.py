"""告警按原因合并。

同一原因在未恢复期间合并成一条：累加去重后的影响主机数，保留首末时间和
首末条告警原文，影响面按站点列出。恢复（resolved）时统一关闭该条目；恢复
之后同一原因再次出现，另开一条单独计数，不并入已关闭的条目。合并错了可以
用 split() 拆开，每条原始告警各成一条。级别不在此判定，由值班确认。
"""

_ENTRIES = []
_NEXT_ID = 1


def reset():
    """清空已登记的告警。"""
    global _NEXT_ID
    _ENTRIES.clear()
    _NEXT_ID = 1


def _open_entry(reason):
    """找该原因当前未关闭（firing）的合并条目，没有则返回 None。"""
    for entry in reversed(_ENTRIES):
        if entry["reason"] == reason and entry["status"] == "firing":
            return entry
    return None


def _new_entry(reason):
    global _NEXT_ID
    entry = {
        "id": _NEXT_ID,
        "reason": reason,
        "status": "firing",
        "hosts": [],
        "sites": {},
        "first_time": "",
        "last_time": "",
        "first_alert": None,
        "last_alert": None,
        "alert_count": 0,
        "alerts": [],
    }
    _NEXT_ID += 1
    return entry


def _record(entry, alert):
    """把一条原始告警并入合并条目，原文完整保留。"""
    raw = dict(alert)
    entry["alerts"].append(raw)
    entry["alert_count"] += 1
    if entry["first_alert"] is None:
        entry["first_alert"] = raw
        entry["first_time"] = raw.get("time", "")
    entry["last_alert"] = raw
    if raw.get("time"):
        entry["last_time"] = raw["time"]
    host = raw["host"]
    if host not in entry["hosts"]:
        entry["hosts"].append(host)
    site_hosts = entry["sites"].setdefault(raw.get("site", ""), [])
    if host not in site_hosts:
        site_hosts.append(host)


def emit(alerts):
    """登记告警，按原因并入未关闭的合并条目；resolved 统一关闭该原因的条目。"""
    if not isinstance(alerts, list):
        raise ValueError("alerts must be a list")
    accepted = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            raise ValueError("alert must be a dict")
        for key in ("reason", "host"):
            if not alert.get(key):
                raise ValueError("alert needs reason and host")
        entry = _open_entry(alert["reason"])
        if entry is None:
            entry = _new_entry(alert["reason"])
            _ENTRIES.append(entry)
        _record(entry, alert)
        if alert.get("status", "firing") == "resolved":
            entry["status"] = "resolved"
        accepted += 1
    return {"accepted": accepted, "total": len(_ENTRIES)}


def report():
    """返回合并条目、去重后的影响主机数、首末时间和恢复状态，按原因汇总。"""
    hosts = set()
    by_reason = {}
    entries = []
    for entry in _ENTRIES:
        hosts.update(entry["hosts"])
        by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1
        entries.append({
            "id": entry["id"],
            "reason": entry["reason"],
            "status": entry["status"],
            "hosts": list(entry["hosts"]),
            "host_count": len(entry["hosts"]),
            "sites": {site: list(site_hosts) for site, site_hosts in entry["sites"].items()},
            "first_time": entry["first_time"],
            "last_time": entry["last_time"],
            "first_alert": dict(entry["first_alert"]),
            "last_alert": dict(entry["last_alert"]),
            "alert_count": entry["alert_count"],
            "alerts": [dict(raw) for raw in entry["alerts"]],
        })
    return {
        "total": len(entries),
        "hosts": len(hosts),
        "by_reason": by_reason,
        "entries": entries,
    }


def split(entry_id):
    """把合并错的条目拆开，每条原始告警各成一条，返回新条目的 id 列表。"""
    for index, entry in enumerate(_ENTRIES):
        if entry["id"] == entry_id:
            break
    else:
        raise ValueError("no such entry: %r" % (entry_id,))
    raw_alerts = entry["alerts"]
    del _ENTRIES[index]
    created = []
    for raw in raw_alerts:
        new_entry = _new_entry(raw["reason"])
        _record(new_entry, raw)
        if raw.get("status", "firing") == "resolved":
            new_entry["status"] = "resolved"
        created.append(new_entry)
    _ENTRIES[index:index] = created
    return {"split": entry_id, "created": [e["id"] for e in created]}
