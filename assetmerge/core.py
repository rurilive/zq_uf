"""按记录整理资产，同一主机仍按多条记录下发。"""

_RECORDS = []


def merge(assets):
    """把资产表原样整理成待下发列表。"""
    if not isinstance(assets, list):
        raise ValueError("assets 必须是列表")
    rows = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError("资产记录必须是字典")
        host = asset.get("host") or asset.get("value")
        if not host:
            raise ValueError("资产记录缺少主机标识")
        rows.append({
            "record": asset.get("id", "r%d" % index),
            "host": host,
            "kind": asset.get("kind", "unknown"),
            "value": asset.get("value", host),
            "vulns": list(asset.get("vulns", [])),
        })
    _RECORDS[:] = rows
    return {
        "records": [dict(row) for row in rows],
        "count": len(rows),
        "alias_count": 0,
        "basis": "record",
        "splits": [],
    }


def report():
    """按记录汇总，漏洞按出现次数累加。"""
    vulns = {}
    for row in _RECORDS:
        for name in row["vulns"]:
            vulns[name] = vulns.get(name, 0) + 1
    return {
        "hosts": len(_RECORDS),
        "records": len(_RECORDS),
        "alias_count": 0,
        "basis": "record",
        "vulns": vulns,
        "vuln_total": sum(vulns.values()),
    }


def scan_list():
    """返回按记录编号升序的下发清单。"""
    return [row["record"] for row in sorted(_RECORDS, key=lambda row: row["record"])]
