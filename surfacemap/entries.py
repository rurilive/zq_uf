"""攻击面清单，当前把每次登记的入口原样追加。"""

_ROWS = []


def reset():
    """清空已登记的入口。"""
    _ROWS.clear()


def surface(entries):
    """写入一批入口，同一入口重复登记会各占一行。"""
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("entry must be a dict")
        for key in ("domain", "address"):
            if not entry.get(key):
                raise ValueError("entry needs domain and address")
        _ROWS.append({
            "domain": entry["domain"],
            "address": entry["address"],
            "port": int(entry.get("port", 443)),
            "owner": entry.get("owner") or "unknown",
            "source": entry.get("source") or "manual",
            "status": "active",
        })
    return {"added": len(entries), "total": len(_ROWS)}


def report():
    """按归属和来源统计行数。"""
    owners = {}
    sources = {}
    for row in _ROWS:
        owners[row["owner"]] = owners.get(row["owner"], 0) + 1
        sources[row["source"]] = sources.get(row["source"], 0) + 1
    return {
        "total": len(_ROWS),
        "active": len(_ROWS),
        "by_owner": owners,
        "sources": sources,
        "entries": [dict(row) for row in _ROWS],
    }
