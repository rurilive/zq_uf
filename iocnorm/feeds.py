"""情报指标归一与匹配。

按指标类型（地址 / 域名 / 样本摘要）分别归一；同一指标保留全部来源
及各自的可信度和有效期，条目有效期取各来源中最晚者。过期指标自动
退出拦截列表（match 不命中、export 不导出），可单独清理，清理记录
保留并支持恢复。来源可信度可人工调整。
"""

import ipaddress

_LIBRARY = {}
_CLEANED = []
_CONFIDENCE_OVERRIDE = {}

_HASH_LENGTHS = {32, 40, 64}
_HEX = set("0123456789abcdefABCDEF")


def _classify(raw):
    """返回 (type, 归一值)；无法识别时返回 (None, None)。"""
    value = str(raw).strip()
    if not value:
        return None, None
    try:
        return "ip", str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if len(value) in _HASH_LENGTHS and all(c in _HEX for c in value):
        return "hash", value.lower()
    domain = value.lower().rstrip(".")
    if domain and " " not in domain:
        return "domain", domain
    return None, None


def _merge_expires(a, b):
    """同一来源重复出现时合并有效期，None 表示不过期。"""
    if a is None or b is None:
        return None
    return max(a, b)


def _entry_expires(entry):
    expires = [s["expires"] for s in entry["sources"]]
    if not expires or any(e is None for e in expires):
        return None
    return max(expires)


def _is_expired(entry, day):
    expires = entry["expires"]
    return day is not None and expires is not None and day > expires


def _sorted_sources(entry):
    """多来源按可信度降序，同分并列按来源名排序。"""
    return sorted(entry["sources"], key=lambda s: (-s["confidence"], s["source"]))


def _refresh(entry):
    entry["expires"] = _entry_expires(entry)


def normalize(feeds, day=None):
    """归一一批情报。返回归一数量、新增数量、来源分布和过期数量。"""
    if not isinstance(feeds, list):
        raise ValueError("feeds must be a list")
    added = 0
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ValueError("feed must be a dict")
        ioc_type, value = _classify(feed.get("value", ""))
        if value is None:
            continue
        source = str(feed.get("source", "")).strip()
        record = {
            "source": source,
            "confidence": _CONFIDENCE_OVERRIDE.get(source, feed.get("confidence", 0)),
            "expires": feed.get("expires"),
        }
        key = (ioc_type, value)
        entry = _LIBRARY.get(key)
        if entry is None:
            entry = {"value": value, "type": ioc_type, "sources": [], "expires": None}
            _LIBRARY[key] = entry
            added += 1
        existing = next((s for s in entry["sources"] if s["source"] == source), None)
        if existing is None:
            entry["sources"].append(record)
        else:
            existing["confidence"] = record["confidence"]
            existing["expires"] = _merge_expires(existing["expires"], record["expires"])
        _refresh(entry)
    distribution = {}
    for entry in _LIBRARY.values():
        for s in entry["sources"]:
            distribution[s["source"]] = distribution.get(s["source"], 0) + 1
    expired = sum(1 for e in _LIBRARY.values() if _is_expired(e, day))
    return {
        "normalized": len(_LIBRARY),
        "added": added,
        "sources": distribution,
        "expired": expired,
    }


def match(ioc, day=None):
    """匹配单个指标。过期条目不命中；命中时返回按可信度排序的全部来源。"""
    ioc_type, value = _classify(ioc)
    result = {
        "ioc": value if value is not None else str(ioc).strip().lower(),
        "type": ioc_type,
        "hit": False,
        "source": "",
        "sources": [],
        "expires": None,
        "expired": False,
    }
    if value is None:
        return result
    entry = _LIBRARY.get((ioc_type, value))
    if entry is None:
        return result
    result["expires"] = entry["expires"]
    if _is_expired(entry, day):
        result["expired"] = True
        return result
    sources = _sorted_sources(entry)
    result["hit"] = True
    result["sources"] = sources
    result["source"] = sources[0]["source"] if sources else ""
    return result


def export(day=None):
    """导出拦截列表：只含未过期条目，与情报库保持一致。"""
    items = []
    for key in sorted(_LIBRARY):
        entry = _LIBRARY[key]
        if _is_expired(entry, day):
            continue
        sources = _sorted_sources(entry)
        items.append({
            "value": entry["value"],
            "type": entry["type"],
            "source": sources[0]["source"] if sources else "",
            "sources": sources,
            "expires": entry["expires"],
        })
    return {"count": len(items), "items": items}


def expiring(day, within):
    """按天筛选：列出 day 之后 within 天内过期的条目。"""
    items = []
    for key in sorted(_LIBRARY):
        entry = _LIBRARY[key]
        expires = entry["expires"]
        if expires is not None and day < expires <= day + within:
            items.append({
                "value": entry["value"],
                "type": entry["type"],
                "expires": expires,
            })
    return {"count": len(items), "items": items}


def purge(day=None):
    """清理过期条目，清理记录保留，误删可用 restore 恢复。"""
    values = []
    for key, entry in list(_LIBRARY.items()):
        if _is_expired(entry, day):
            _CLEANED.append({"entry": entry, "purged_at": day})
            values.append(entry["value"])
            del _LIBRARY[key]
    return {"purged": len(values), "values": values}


def cleaned():
    """查看清理记录。"""
    return {
        "count": len(_CLEANED),
        "records": [
            {"value": r["entry"]["value"], "type": r["entry"]["type"],
             "purged_at": r["purged_at"]}
            for r in _CLEANED
        ],
    }


def restore(ioc):
    """恢复最近一条被清理的同名条目。"""
    ioc_type, value = _classify(ioc)
    for i in range(len(_CLEANED) - 1, -1, -1):
        entry = _CLEANED[i]["entry"]
        if entry["type"] == ioc_type and entry["value"] == value:
            _LIBRARY[(entry["type"], entry["value"])] = entry
            del _CLEANED[i]
            return {"restored": True, "value": value}
    return {"restored": False, "value": value}


def set_confidence(source, confidence):
    """人工调整某来源的可信度，作用于库里已有的全部条目及后续归一。"""
    _CONFIDENCE_OVERRIDE[source] = confidence
    updated = 0
    for entry in _LIBRARY.values():
        for s in entry["sources"]:
            if s["source"] == source:
                s["confidence"] = confidence
                updated += 1
    return {"source": source, "confidence": confidence, "updated": updated}
