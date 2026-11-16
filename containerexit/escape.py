"""容器逃逸检测，当前只看特权模式。"""

_CONTAINERS = {}

_ADVICE = {
    "high": "restart without privileged",
    "low": "no action",
}


def reset():
    """清空容器记录。"""
    _CONTAINERS.clear()
    return {"containers": 0}


def check(container):
    """特权就报高危，其余一律低危，其他配置不看。"""
    if not isinstance(container, dict) or not container.get("name"):
        raise ValueError("container is required")
    privileged = bool(container.get("privileged"))
    level = "high" if privileged else "low"
    record = {
        "container": container["name"],
        "host": container.get("host", ""),
        "hit": privileged,
        "level": level,
        "basis": [{"key": "privileged", "value": privileged}],
        "advice": _ADVICE[level],
    }
    _CONTAINERS[(record["host"], record["container"])] = record
    return dict(record)


def report():
    """按容器逐条列出，不按主机合并。"""
    records = list(_CONTAINERS.values())
    return {
        "containers": len(records),
        "high": sum(1 for record in records if record["level"] == "high"),
        "hosts": len({record["host"] for record in records}),
        "merged": [],
    }
