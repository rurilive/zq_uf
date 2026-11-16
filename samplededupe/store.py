"""样本入库与家族报表，当前只按完整哈希判重。"""

_ITEMS = []
_DUPLICATES = 0


def reset():
    """清空内存中的样本和判重计数。"""
    global _DUPLICATES
    _ITEMS.clear()
    _DUPLICATES = 0


def ingest(sample):
    """写入样本，哈希相同即视为同一份。"""
    global _DUPLICATES
    if not isinstance(sample, dict):
        raise ValueError("sample must be a dict")
    name = sample.get("name")
    digest = sample.get("digest")
    if not name or not digest:
        raise ValueError("sample needs name and digest")
    for item in _ITEMS:
        if item["digest"] == digest:
            _DUPLICATES += 1
            return {
                "status": "duplicate",
                "name": name,
                "family": item["family"],
                "added": False,
            }
    family = sample.get("family") or "unlabeled"
    _ITEMS.append({
        "name": name,
        "digest": digest,
        "family": family,
        "size": int(sample.get("size", 0)),
    })
    return {"status": "added", "name": name, "family": family, "added": True}


def family_view(family):
    """按家族列出已入库的样本名。"""
    names = [item["name"] for item in _ITEMS if item["family"] == family]
    return {"family": family, "samples": names}


def report():
    """汇总入库总量、重复次数和家族分布。"""
    groups = {}
    for item in _ITEMS:
        groups.setdefault(item["family"], []).append(item["name"])
    return {
        "total": len(_ITEMS),
        "duplicates": _DUPLICATES,
        "families": {name: len(names) for name, names in sorted(groups.items())},
        "samples": [item["name"] for item in _ITEMS],
    }
