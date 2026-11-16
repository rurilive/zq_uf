"""配置基线比对：登记基线与允许差异范围，按主机跟踪漂移、整改与重复漂移。"""

from datetime import datetime, timezone

_baseline = {}
_ranges = {}
_hosts = {}  # host -> {key: {"status": ok|drift|fixed, "current", "repeats"}}
_events = []  # 漂移/整改事件，按时间回看


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_spec(spec):
    if isinstance(spec, (list, tuple, set)):
        return {"type": "values", "values": list(spec)}
    if isinstance(spec, dict):
        if "values" in spec:
            return {"type": "values", "values": list(spec["values"])}
        if "tolerance" in spec:
            return {"type": "tolerance", "tolerance": float(spec["tolerance"])}
        if "min" in spec or "max" in spec:
            return {"type": "bounds", "min": spec.get("min"), "max": spec.get("max")}
    raise ValueError("无法识别的差异范围")


def _allowed(key, value):
    spec = _ranges.get(key)
    if value == _baseline[key]:
        return True
    if spec is None:
        return False
    if spec["type"] == "values":
        return value in spec["values"]
    try:
        cur = float(value)
        base = float(_baseline[key])
    except (TypeError, ValueError):
        return False
    if spec["type"] == "tolerance":
        return abs(cur - base) <= spec["tolerance"]
    if spec["min"] is not None and cur < float(spec["min"]):
        return False
    if spec["max"] is not None and cur > float(spec["max"]):
        return False
    return True


def baseline(config, ranges=None):
    if not isinstance(config, dict):
        raise ValueError("config 需要字典")
    _baseline.clear()
    _baseline.update(config)
    _ranges.clear()
    if ranges is not None:
        if not isinstance(ranges, dict):
            raise ValueError("ranges 需要字典")
        for key, spec in ranges.items():
            if key not in _baseline:
                raise ValueError(f"ranges 中存在基线外的配置项: {key}")
            _ranges[key] = _normalize_spec(spec)
    return {"keys": sorted(_baseline), "count": len(_baseline)}


def set_range(key, spec, confirm=False):
    """调整允许差异范围，需二次确认：先调用返回 pending，再 confirm=True 生效。"""
    if key not in _baseline:
        raise ValueError(f"未知配置项: {key}")
    normalized = _normalize_spec(spec)
    if not confirm:
        return {
            "pending": True,
            "key": key,
            "spec": normalized,
            "message": "范围调整需二次确认，请使用 confirm=True 再次调用",
        }
    _ranges[key] = normalized
    return {"pending": False, "key": key, "spec": normalized}


def diff(host):
    if not isinstance(host, dict):
        raise ValueError("host 需要字典")
    name = host.get("host")
    if not name:
        raise ValueError("缺少主机名")
    current = host.get("config", {})
    state = _hosts.setdefault(name, {})
    items = []
    for key in sorted(_baseline):
        value = current.get(key)
        entry = state.setdefault(key, {"status": "ok", "current": value, "repeats": 0})
        if _allowed(key, value):
            if entry["status"] == "drift":
                entry["status"] = "fixed"
                _events.append({"time": _now(), "host": name, "key": key,
                                "type": "fixed", "current": value,
                                "repeats": entry["repeats"]})
            entry["current"] = value
            continue
        if entry["status"] != "drift":
            entry["repeats"] += 1
            _events.append({"time": _now(), "host": name, "key": key,
                            "type": "drift", "current": value,
                            "repeats": entry["repeats"]})
        entry["status"] = "drift"
        entry["current"] = value
        items.append({
            "key": key,
            "host": name,
            "baseline": _baseline[key],
            "current": value,
            "repeats": entry["repeats"],
            "fixed": False,
        })
    return {"host": name, "drift": items, "passed": not items}


def report(key=None, host=None):
    items = []
    for name in sorted(_hosts):
        if host is not None and name != host:
            continue
        for k in sorted(_hosts[name]):
            if key is not None and k != key:
                continue
            entry = _hosts[name][k]
            if entry["status"] == "ok":
                continue
            items.append({
                "key": k,
                "host": name,
                "baseline": _baseline.get(k),
                "current": entry["current"],
                "repeats": entry["repeats"],
                "fixed": entry["status"] == "fixed",
            })
    by_key = {}
    by_host = {}
    repeats = {}
    for item in items:
        if not item["fixed"]:
            by_key[item["key"]] = by_key.get(item["key"], 0) + 1
            by_host[item["host"]] = by_host.get(item["host"], 0) + 1
        if item["repeats"] > 1:
            repeats[item["key"]] = repeats.get(item["key"], 0) + item["repeats"] - 1
    return {
        "key": key,
        "host": host,
        "items": items,
        "hosts": sorted(_hosts),
        "by_key": by_key,
        "by_host": by_host,
        "repeats": repeats,
    }


def history(key=None, host=None):
    """按时间回看漂移/整改事件，可按配置项和主机过滤。"""
    return [
        dict(e) for e in _events
        if (key is None or e["key"] == key) and (host is None or e["host"] == host)
    ]
