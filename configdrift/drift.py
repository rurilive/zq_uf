"""配置基线比对，当前逐项要求严格相等。"""

_baseline = {}
_runs = []


def baseline(config):
    if not isinstance(config, dict):
        raise ValueError("config 需要字典")
    _baseline.clear()
    _baseline.update(config)
    return {"keys": sorted(_baseline), "count": len(_baseline)}


def diff(host):
    if not isinstance(host, dict):
        raise ValueError("host 需要字典")
    name = host.get("host")
    if not name:
        raise ValueError("缺少主机名")
    current = host.get("config", {})
    items = []
    for key in sorted(_baseline):
        if current.get(key) != _baseline[key]:
            items.append(
                {
                    "key": key,
                    "host": name,
                    "baseline": _baseline[key],
                    "current": current.get(key),
                    "repeats": 1,
                    "fixed": False,
                }
            )
    _runs.append({"host": name, "drift": items})
    return {"host": name, "drift": items, "passed": not items}


def report(key=None):
    items = []
    for run in _runs:
        for item in run["drift"]:
            if key is None or item["key"] == key:
                items.append(dict(item))
    return {
        "key": key,
        "items": items,
        "hosts": sorted({run["host"] for run in _runs}),
        "repeats": {},
    }
