"""情报归一，当前只做小写和去重。"""

_LIBRARY = {}


def normalize(feeds, day=None):
    """同一指标只留第一次出现的来源，类型和有效期都不管。"""
    if not isinstance(feeds, list):
        raise ValueError("feeds must be a list")
    added = 0
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ValueError("feed must be a dict")
        value = str(feed.get("value", "")).strip().lower()
        if not value:
            continue
        if value in _LIBRARY:
            continue
        _LIBRARY[value] = {
            "value": value,
            "type": "unknown",
            "sources": [feed.get("source", "")],
            "expires": feed.get("expires"),
            "confidence": feed.get("confidence", 0),
        }
        added += 1
    return {
        "normalized": len(_LIBRARY),
        "added": added,
        "sources": {},
        "expired": 0,
    }


def match(ioc, day=None):
    """只做整串相等比较，过期条目照样命中。"""
    value = str(ioc).strip().lower()
    entry = _LIBRARY.get(value)
    if entry is None:
        return {
            "ioc": value,
            "hit": False,
            "source": "",
            "expires": None,
        }
    return {
        "ioc": value,
        "hit": True,
        "source": entry["sources"][0],
        "expires": entry["expires"],
    }


def export(day=None):
    """照原样导出，day 参数不参与过滤。"""
    items = []
    for value in sorted(_LIBRARY):
        entry = _LIBRARY[value]
        items.append({
            "value": entry["value"],
            "type": entry["type"],
            "source": entry["sources"][0],
            "expires": entry["expires"],
        })
    return {"count": len(items), "items": items}
