"""终端基线，当前一份规则跑所有系统。"""

_BASELINE = {
    "firewall": "on",
    "antivirus": "on",
    "ssh_root_login": "no",
}

_HOSTS = {}


def reset():
    """清空终端记录。"""
    _HOSTS.clear()
    return {"hosts": 0}


def check(host):
    """同一份期望值套到所有系统，缺项直接算未通过。"""
    if not isinstance(host, dict) or not host.get("host"):
        raise ValueError("host is required")
    items = host.get("items") or {}
    passed = []
    failed = []
    for name, expected in _BASELINE.items():
        if items.get(name) == expected:
            passed.append(name)
        else:
            failed.append(name)
    record = {
        "host": host["host"],
        "os": host.get("os", ""),
        "passed": passed,
        "failed": failed,
        "not_applicable": [],
        "recheck": [],
    }
    _HOSTS[host["host"]] = record
    return dict(record)


def report():
    """通过率把所有项都算进分母。"""
    records = list(_HOSTS.values())
    total = sum(len(record["passed"]) + len(record["failed"]) for record in records)
    passed = sum(len(record["passed"]) for record in records)
    return {
        "hosts": len(records),
        "checks": total,
        "passed": passed,
        "rate": round(passed / total, 3) if total else 0.0,
        "not_applicable": 0,
        "recheck": [],
    }
