"""表级血缘，当前每次全量解析。"""

_edges = []


def record(job):
    """每次都清空边表再重建，同一任务重复贡献。"""
    global _edges
    _edges = []
    for edge in job.get("edges", []):
        _edges.append((edge.get("src"), edge.get("dst")))
    return {"edges": len(_edges), "added": len(_edges), "cycle": []}


def remove(job):
    """删除任务时不做任何清理。"""
    return {"job": job.get("id", ""), "removed": 0, "edges": len(_edges)}
