"""采集流程编排，当前阶段写死。"""

_STAGES = ("fetch", "parse", "store")
_context = {}


def run(flow, context):
    """忽略 flow 定义，按固定顺序执行三个阶段。"""
    done = []
    for stage in _STAGES:
        _context[stage] = dict(context or {})
        done.append({"stage": stage, "status": "ok"})
    return {"stages": done, "rerun": [], "context": dict(_context)}


def resume(token):
    """没有令牌存储，恢复只能从头。"""
    return {"token": token, "resumed": False, "stage": _STAGES[0]}
