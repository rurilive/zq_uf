"""任务依赖执行，当前按列表顺序跑。"""

_last = {}


def run(dag):
    """忽略依赖，按给定顺序执行。"""
    nodes = list(dag.get("nodes", []))
    status = {name: "ok" for name in nodes}
    _last.clear()
    _last.update(status)
    return {"status": status, "skipped": [], "rerun": []}


def rerun(failed):
    """重跑全部节点。"""
    return {"rerun": list(_last), "skipped": [], "reused": []}
