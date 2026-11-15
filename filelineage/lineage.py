"""文件血缘，当前只到任务级且只查一跳。"""

_jobs = {}


def record(job):
    """记录任务读了哪些文件，不建文件之间的边。"""
    name = job.get("id", "")
    _jobs[name] = {
        "reads": list(job.get("reads", [])),
        "writes": list(job.get("writes", [])),
    }
    return {"job": name, "reads": len(_jobs[name]["reads"]), "edges": 0, "path": []}


def impact(path):
    """只找直接读这个文件的任务，不继续向下。"""
    hits = [name for name, job in _jobs.items() if path in job["reads"]]
    return {"path": path, "jobs": hits, "depth": 1, "paths": [[path, name] for name in hits]}
