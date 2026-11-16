"""沙箱报告解析，当前按原文顺序摊平行为条目。"""

_REPORTS = {}


def reset():
    """清空已解析的报告。"""
    _REPORTS.clear()


def parse(report):
    """摊平报告里的行为条目，不合并也不排序。"""
    if not isinstance(report, dict):
        raise ValueError("report must be a dict")
    sample_id = report["id"]
    items = []
    for entry in report["behaviors"]:
        items.append({
            "type": entry["type"],
            "action": entry["action"],
            "seconds": entry["seconds"],
        })
    result = {"id": sample_id, "behaviors": items, "static": dict(report["static"])}
    _REPORTS[sample_id] = result
    return result


def summary(sample_id):
    """按样本给出行为条数和类型分布。"""
    if sample_id not in _REPORTS:
        raise ValueError("unknown sample id")
    result = _REPORTS[sample_id]
    counts = {}
    for item in result["behaviors"]:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return {"id": sample_id, "total": len(result["behaviors"]), "by_type": counts}


def types():
    """列出已出现过的行为类型。"""
    seen = []
    for result in _REPORTS.values():
        for item in result["behaviors"]:
            if item["type"] not in seen:
                seen.append(item["type"])
    return {"types": seen}
