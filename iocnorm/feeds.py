"""情报指标归一与匹配。

按指标类型（ipv4 / domain / md5 / sha1 / sha256）分别归一：
同一指标保留全部来源和可信度，按可信度排序，冲突来源并列；
指标带有效期，过期自动退出拦截列表（match / export 按 day 过滤）；
cleanup 清理过期条目并保留记录，restore 可恢复；
set_confidence 支持人工调整来源可信度。
"""

import re

_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_HASH_TYPES = ((64, "sha256"), (40, "sha1"), (32, "md5"))

_LIBRARY = {}
_CLEANED = []
_CONFIDENCE_OVERRIDE = {}


def _normalize_value(ioc):
    return str(ioc).strip().lower().rstrip(".")


def _detect_type(value):
    m = _IPV4_RE.match(value)
    if m and all(int(g) <= 255 for g in m.groups()):
        return "ipv4"
    if value and all(c in "0123456789abcdef" for c in value):
        for size, name in _HASH_TYPES:
            if len(value) == size:
                return name
    if _DOMAIN_RE.match(value):
        return "domain"
    return "unknown"


def _eff_conf(record):
    return _CONFIDENCE_OVERRIDE.get(record["source"], record["confidence"])


def _sort_sources(entry):
    # 稳定排序：可信度相同（冲突）的来源保持并列，按入库先后排列
    entry["sources"].sort(key=lambda s: -_eff_conf(s))


def _refresh_expires(entry):
    expires = [s["expires"] for s in entry["sources"]]
    if not expires or any(e is None for e in expires):
        entry["expires"] = None
    else:
        entry["expires"] = max(expires)


def _expired(entry, day):
    if day is None or entry["expires"] is None:
        return False
    return day > entry["expires"]


def _merge_source(entry, source):
    for record in entry["sources"]:
        if record["source"] == source["source"]:
            record["confidence"] = source["confidence"]
            if record["expires"] is None or source["expires"] is None:
                record["expires"] = None
            else:
                record["expires"] = max(record["expires"], source["expires"])
            return
    entry["sources"].append(source)


def _merge_entry(entry):
    key = (entry["type"], entry["value"])
    existing = _LIBRARY.get(key)
    if existing is None:
        _sort_sources(entry)
        _refresh_expires(entry)
        _LIBRARY[key] = entry
        return False
    for source in entry["sources"]:
        _merge_source(existing, source)
    _sort_sources(existing)
    _refresh_expires(existing)
    return True


def normalize(feeds, day=None):
    """归一一批情报，返回归一数量、来源分布和过期情况。"""
    if not isinstance(feeds, list):
        raise ValueError("feeds must be a list")
    added = 0
    updated = 0
    sources = {}
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ValueError("feed must be a dict")
        value = _normalize_value(feed.get("value", ""))
        if not value:
            continue
        entry = {
            "value": value,
            "type": _detect_type(value),
            "sources": [{
                "source": feed.get("source", ""),
                "confidence": feed.get("confidence", 0),
                "expires": feed.get("expires"),
            }],
            "expires": None,
        }
        sources[entry["sources"][0]["source"]] = (
            sources.get(entry["sources"][0]["source"], 0) + 1
        )
        if _merge_entry(entry):
            updated += 1
        else:
            added += 1
    expired = sum(1 for e in _LIBRARY.values() if _expired(e, day))
    return {
        "normalized": len(_LIBRARY),
        "added": added,
        "updated": updated,
        "sources": sources,
        "expired": expired,
    }


def _source_views(entry, day):
    views = []
    for record in entry["sources"]:
        active = record["expires"] is None or day is None or day <= record["expires"]
        views.append({
            "source": record["source"],
            "confidence": _eff_conf(record),
            "expires": record["expires"],
            "active": active,
        })
    return views


def match(ioc, day=None):
    """匹配单个指标，过期条目不命中，返回全部来源（按可信度排序）。"""
    value = _normalize_value(ioc)
    itype = _detect_type(value)
    entry = _LIBRARY.get((itype, value))
    if entry is None:
        return {
            "ioc": value,
            "type": itype,
            "hit": False,
            "source": "",
            "sources": [],
            "expires": None,
            "expired": False,
        }
    sources = _source_views(entry, day)
    active = [s for s in sources if s["active"]]
    return {
        "ioc": value,
        "type": itype,
        "hit": bool(active),
        "source": active[0]["source"] if active else "",
        "sources": sources,
        "expires": entry["expires"],
        "expired": _expired(entry, day),
    }


def export(day=None):
    """导出拦截列表，只含当日有效的条目，与情报库保持一致。"""
    items = []
    for key in sorted(_LIBRARY):
        entry = _LIBRARY[key]
        if _expired(entry, day):
            continue
        sources = _source_views(entry, day)
        items.append({
            "value": entry["value"],
            "type": entry["type"],
            "source": sources[0]["source"],
            "sources": sources,
            "confidence": sources[0]["confidence"],
            "expires": entry["expires"],
        })
    return {"count": len(items), "items": items}


def cleanup(day=None):
    """清理过期条目，记录保留在 _CLEANED，可用 restore 恢复。"""
    removed = [key for key, entry in _LIBRARY.items() if _expired(entry, day)]
    for key in removed:
        _CLEANED.append({"entry": _LIBRARY.pop(key), "day": day})
    return {"cleaned": len(removed)}


def restore(values=None):
    """恢复被清理的条目；values 给出时只恢复指定指标。"""
    restored = 0
    kept = []
    for record in _CLEANED:
        entry = record["entry"]
        if values is not None and entry["value"] not in values:
            kept.append(record)
            continue
        _merge_entry(entry)
        restored += 1
    _CLEANED[:] = kept
    return {"restored": restored}


def set_confidence(source, confidence):
    """人工调整某来源的可信度；confidence 为 None 时恢复各条目原值。"""
    if confidence is None:
        _CONFIDENCE_OVERRIDE.pop(source, None)
    else:
        _CONFIDENCE_OVERRIDE[source] = confidence
    updated = 0
    for entry in _LIBRARY.values():
        if any(s["source"] == source for s in entry["sources"]):
            _sort_sources(entry)
            updated += 1
    return {"source": source, "confidence": confidence, "updated": updated}
