"""多步分步提交流程：按批次保存条件与令牌。

- 条件按批次（flow）逐步保存，批次之间隔离；
- 回退到某一步时，后面的条件作废，令牌重新获取；
- 同一批次重复提交（同一令牌）只产生一套结果；
- 提交失败保留已选内容（draft），恢复后从原步骤继续；
- 批次结束后条件可导出，导入新批次后从原步骤继续。
"""

_flows = {}


def _fresh(flow):
    return {"step": 0, "nonce": 0, "token": "%s-0" % flow,
            "conditions": {}, "draft": None, "done": False, "used": {}}


def _state(st):
    merged = {}
    for i in sorted(st["conditions"]):
        merged.update(st["conditions"][i])
    return merged


def _resp(flow, st, ok=True, back=False, replayed=False):
    return {"flow": flow, "step": st["step"], "token": st["token"],
            "state": _state(st), "ok": ok, "back": back, "replayed": replayed}


def _advance(flow, st):
    st["nonce"] += 1
    st["token"] = "%s-%d" % (flow, st["nonce"])


def begin(flow):
    """开始批次：步骤、条件、令牌从零初始化。"""
    st = _fresh(flow)
    _flows[flow] = st
    return _resp(flow, st)


def step(flow, data, token=None, fail=False):
    """提交当前步条件。

    token 带上上一次返回的令牌；已消费的令牌再次提交判为重复，
    直接返回首次结果（replayed=True），不产生第二套结果。
    fail=True 模拟提交失败：条件存入 draft 保留，步骤不动，
    恢复后用同样的调用从原步骤继续。
    """
    st = _flows.setdefault(flow, _fresh(flow))
    if token is not None and token in st["used"]:
        resp = dict(st["used"][token])
        resp["replayed"] = True
        return resp
    if fail:
        st["draft"] = dict(data)
        resp = _resp(flow, st, ok=False)
        resp["failed_step"] = st["step"]
        resp["failed_condition"] = dict(data)
        return resp
    st["conditions"][st["step"]] = dict(data)
    st["step"] += 1
    st["draft"] = None
    _advance(flow, st)
    resp = _resp(flow, st)
    if token is not None:
        st["used"][token] = dict(resp)
    return resp


def back(flow, to_step=None):
    """回退到 to_step（默认上一步）：之后的条件作废，令牌重取。"""
    st = _flows.setdefault(flow, _fresh(flow))
    if to_step is None:
        to_step = st["step"] - 1
    to_step = max(0, min(to_step, st["step"]))
    st["conditions"] = {i: c for i, c in st["conditions"].items() if i < to_step}
    st["step"] = to_step
    st["draft"] = None
    _advance(flow, st)
    return _resp(flow, st, back=True)


def finish(flow):
    """结束批次，之后条件可导出复用。"""
    st = _flows.setdefault(flow, _fresh(flow))
    st["done"] = True
    return _resp(flow, st)


def export(flow):
    """导出批次条件（可 JSON 序列化），供下一批复用。"""
    st = _flows[flow]
    return {"step": st["step"],
            "conditions": {i: dict(c) for i, c in st["conditions"].items()}}


def import_conditions(flow, data):
    """把导出的条件导入新批次，从原步骤继续；不覆盖已有批次。"""
    if flow in _flows:
        return {"flow": flow, "ok": False, "error": "batch exists"}
    st = _fresh(flow)
    st["conditions"] = {int(i): dict(c) for i, c in data["conditions"].items()}
    st["step"] = int(data["step"])
    _flows[flow] = st
    resp = _resp(flow, st)
    resp["imported"] = True
    return resp


def copy_to(src, dst):
    """把 src 批次的条件复制到新批次 dst。"""
    if dst in _flows:
        return {"flow": dst, "ok": False, "error": "batch exists"}
    return import_conditions(dst, export(src))
