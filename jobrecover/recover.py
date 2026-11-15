"""采集任务阶段恢复，当前不记阶段。"""

_points = {}


def save(job, stage, state):
    """只存阶段名和状态，没有位点和校验和。"""
    _points[job] = {"stage": stage, "state": dict(state)}
    return {"job": job, "stage": stage, "saved": True, "checksum": None}


def resume(job):
    """恢复一律从起始阶段重来。"""
    return {"job": job, "stage": "start", "skipped": [], "restored": False}
