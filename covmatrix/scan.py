"""目标扫描调度，当前所有目标共用一档固定速率。"""

DEFAULT_RATE = 100

_scans = {}


def _blank_record(host, port):
    """每次扫描都从零开始的固定档位记录。"""
    return {
        "target": host,
        "port": port,
        "rate": DEFAULT_RATE,
        "adjustments": 0,
        "throttle": None,
        "resume": 0,
        "progress": 0,
        "paused": False,
        "reason": None,
    }


def scan(target):
    if not isinstance(target, dict):
        raise ValueError("target 需要字典")
    host = target.get("host")
    if not host:
        raise ValueError("缺少目标地址")
    record = _blank_record(host, target.get("port", 443))
    _scans[host] = record
    return dict(record)


def report(target=None):
    if target is None:
        rows = list(_scans.values())
    else:
        rows = [row for row in _scans.values() if row["target"] == target]
    return {
        "target": target,
        "count": len(rows),
        "targets": [dict(row) for row in rows],
        "adjustments": [],
    }
