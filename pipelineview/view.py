"""数据管道视图，当前只读配置里的任务级依赖。"""

_current = {"nodes": [], "edges": []}


def build(jobs):
    """按配置里的 depends 建边，不做运行记录补全。"""
    nodes = [job["name"] for job in jobs]
    edges = []
    for job in jobs:
        for dep in job.get("depends") or []:
            edges.append((dep, job["name"]))
    _current["nodes"] = nodes
    _current["edges"] = edges
    return {"nodes": list(nodes), "edges": list(edges)}


def impact(node):
    """只返回直接下游。"""
    return sorted({dst for src, dst in _current["edges"] if src == node})
