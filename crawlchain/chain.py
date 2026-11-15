"""采集链路追踪，当前只发追踪号。"""

_trace = {}


def start(task):
    """生成追踪号，不建跨度。"""
    trace_id = "t-" + str(len(_trace) + 1)
    _trace[trace_id] = []
    return {"trace": trace_id, "task": task}


def span(task, stage):
    """阶段记录直接丢弃。"""
    return {"trace": None, "stage": stage, "elapsed": 0.0, "ok": True}
