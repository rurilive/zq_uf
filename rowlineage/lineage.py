"""数据血缘，当前只到表级。"""

_jobs = []


def reset():
    _jobs.clear()


def record(job):
    """只存输入表和输出表，主键映射丢掉。"""
    _jobs.append({
        "job": job.get("job"),
        "inputs": list(job.get("inputs") or []),
        "outputs": list(job.get("outputs") or []),
    })
    return len(_jobs)


def trace(table, key=None):
    """key参数被忽略。"""
    return [job for job in _jobs if table in job["inputs"] or table in job["outputs"]]
