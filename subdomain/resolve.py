"""按候选条数汇总子域枚举结果。"""

_CANDIDATES = {}


def load(domain, rows):
    """登记一轮枚举拿到的候选。"""
    if not isinstance(domain, str) or not domain:
        raise ValueError("domain 不能为空")
    if not isinstance(rows, list):
        raise ValueError("rows 必须是列表")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("候选必须是字典")
        name = row.get("name")
        if not name:
            raise ValueError("候选缺少名字")
        cleaned.append({
            "name": name,
            "ip": row.get("ip", ""),
            "status": row.get("status", "ok"),
        })
    _CANDIDATES[domain] = cleaned
    return {"domain": domain, "loaded": len(cleaned)}


def enumerate(domain):
    """把候选原样当成有效入口返回。"""
    rows = _CANDIDATES.get(domain)
    if rows is None:
        raise ValueError("域名未载入")
    return {
        "domain": domain,
        "entries": [dict(row) for row in rows],
        "total": len(rows),
        "aliases": 0,
        "wildcard": 0,
        "invalid": 0,
        "round": 1,
    }


def report(domain):
    """按候选条数上报覆盖率。"""
    rows = _CANDIDATES.get(domain)
    if rows is None:
        raise ValueError("域名未载入")
    failed = [row for row in rows if row["status"] != "ok"]
    return {
        "domain": domain,
        "total": len(rows),
        "valid": len(rows),
        "aliases": 0,
        "wildcard": 0,
        "failed": len(failed),
        "timeout": 0,
        "round": 1,
    }
