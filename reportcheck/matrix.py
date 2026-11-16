"""接口检测覆盖登记，当前只收命中的条目。"""

_records = []


def _hit_types(types):
    """只留下执行结果为命中的类型。"""
    covered = []
    for kind, result in types.items():
        if result == "hit":
            covered.append({"type": kind, "result": result})
    return covered


def coverage(api):
    if not isinstance(api, dict):
        raise ValueError("api 需要字典")
    name = api.get("name")
    if not name:
        raise ValueError("缺少接口名")
    types = api.get("types", {})
    covered = _hit_types(types)
    record = {
        "api": name,
        "version": api.get("version"),
        "environment": api.get("environment"),
        "covered": covered,
        "missing": [],
        "skipped": [],
        "total": len(types),
        "pending_new": [],
    }
    _records.append(record)
    return dict(record)


def report(api=None):
    if api is None:
        rows = list(_records)
    else:
        rows = [row for row in _records if row["api"] == api]
    return {
        "api": api,
        "count": len(rows),
        "rows": [dict(row) for row in rows],
        "pending_new": [],
    }
