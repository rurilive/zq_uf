"""漏洞复测记录，当前只看本轮步骤是否全过。"""

_records = []


def _judge(steps):
    """步骤全过算通过，空步骤也按通过处理。"""
    if not steps:
        return True
    return all(bool(step.get("ok")) for step in steps)


def retest(vuln):
    if not isinstance(vuln, dict):
        raise ValueError("vuln 需要字典")
    vuln_id = vuln.get("id")
    if not vuln_id:
        raise ValueError("缺少漏洞编号")
    steps = vuln.get("steps", [])
    passed = _judge(steps)
    record = {
        "vuln": vuln_id,
        "project": vuln.get("project"),
        "at": None,
        "result": "pass" if passed else "fail",
        "step_count": len(steps),
        "diff": [],
        "failures": [],
        "baseline": None,
        "closed": passed,
    }
    _records.append(record)
    return dict(record)


def history(vuln=None):
    if vuln is None:
        rows = list(_records)
    else:
        rows = [row for row in _records if row["vuln"] == vuln]
    return {
        "vuln": vuln,
        "count": len(rows),
        "records": [dict(row) for row in rows],
        "projects": [],
    }
