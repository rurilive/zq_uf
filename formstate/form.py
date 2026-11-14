"""多步表单，当前每次都重新提交。"""


def start(form):
    return {"form": form, "step": 0, "values": {}}


def step(state, values):
    """没有幂等键，重复调用就多提交一次。"""
    state["step"] += 1
    state["values"].update(values)
    return state
