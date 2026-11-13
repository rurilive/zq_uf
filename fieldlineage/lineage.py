"""数据血缘，当前只到表级。"""

_jobs = []


def reset():
    _jobs.clear()


def record(job):
    """存输入表和输出表，字段信息丢掉。"""
    entry = {
        "job": job.get("job"),
        "inputs": list(job.get("inputs") or []),
        "outputs": list(job.get("outputs") or []),
    }
    _jobs.append(entry)
    return len(_jobs)


def trace(table, field=None):
    """field 参数被忽略，只按表名匹配。"""
    rows = []
    for entry in _jobs:
        if table in entry["inputs"] or table in entry["outputs"]:
            rows.append(entry)
    return rows
