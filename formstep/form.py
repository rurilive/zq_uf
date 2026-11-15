"""多步表单流程，当前每步重新取令牌。"""

_flows = {}


def begin(flow):
    """开始流程，令牌从零计数。"""
    _flows[flow] = {"step": 0}
    return {"flow": flow, "step": 0, "token": "%s-0" % flow, "state": {}}


def step(flow, data):
    """每步生成新令牌，不保存上一步状态。"""
    st = _flows.setdefault(flow, {"step": 0})
    st["step"] += 1
    return {"flow": flow, "step": st["step"], "token": "%s-%d" % (flow, st["step"]),
            "state": dict(data), "replayed": False}
