"""多步表单流程：按批次保存令牌与各步条件。

- 条件按批次（flow）保存，批次之间隔离；
- 每步提交携带当前令牌，令牌过期判为重复提交，不产生第二套结果；
- 回退到某一步时，该步及之后的条件作废，令牌重取（页面状态重新获取）；
- 提交失败保留已选内容（draft），令牌不变，恢复后从原步骤继续；
- 批次结束后可导出条件，导入新批次后从原步骤继续，不覆盖已有批次。
"""

import copy
import threading

_lock = threading.Lock()
_flows = {}


def _new_state():
    return {"step": 0, "serial": 0, "conditions": [], "draft": None,
            "failed": None, "done": False}


def _token(flow, serial):
    return "%s-%d" % (flow, serial)


def _view(flow, st, **extra):
    out = {"flow": flow, "step": st["step"], "token": _token(flow, st["serial"]),
           "state": copy.deepcopy(st["conditions"]), "draft": copy.deepcopy(st["draft"]),
           "replayed": False, "rolled_back": False, "ok": True,
           "failed": copy.deepcopy(st["failed"]), "done": st["done"]}
    out.update(extra)
    return out


def _get(flow):
    return _flows.setdefault(flow, _new_state())


def begin(flow):
    """开始批次；批次已存在时返回现状，不覆盖。"""
    with _lock:
        return _view(flow, _get(flow))


def step(flow, data, token=None):
    """提交当前步条件。token 与当前令牌不符判为重复提交；空值判失败并保留草稿。"""
    with _lock:
        st = _get(flow)
        if st["done"]:
            st["failed"] = {"step": st["step"], "field": None, "reason": "batch finished"}
            return _view(flow, st, ok=False)
        if token is not None and token != _token(flow, st["serial"]):
            return _view(flow, st, replayed=True)
        for field, value in data.items():
            if value is None or value == "":
                st["draft"] = dict(data)
                st["failed"] = {"step": st["step"], "field": field, "reason": "empty value"}
                return _view(flow, st, ok=False)
        st["conditions"].append(dict(data))
        st["step"] += 1
        st["serial"] += 1
        st["draft"] = None
        st["failed"] = None
        return _view(flow, st)


def back(flow, to_step):
    """回退到 to_step：该步及之后条件作废，令牌重取，之前的条件保留。"""
    with _lock:
        st = _get(flow)
        if to_step >= st["step"]:
            return _view(flow, st)
        del st["conditions"][to_step:]
        st["step"] = to_step
        st["serial"] += 1
        st["draft"] = None
        st["failed"] = None
        return _view(flow, st, rolled_back=True)


def finish(flow):
    """结束批次，之后不可再提交。"""
    with _lock:
        st = _get(flow)
        st["done"] = True
        return _view(flow, st)


def export(flow):
    """导出批次条件，供下一批复用。"""
    with _lock:
        st = _get(flow)
        return {"flow": flow, "step": st["step"],
                "conditions": copy.deepcopy(st["conditions"]), "done": st["done"]}


def import_flow(flow, data):
    """把导出的条件导入新批次，从原步骤继续；批次已存在则拒绝，不覆盖。"""
    with _lock:
        if flow in _flows:
            raise ValueError("batch already exists: %s" % flow)
        st = _new_state()
        st["conditions"] = copy.deepcopy(list(data["conditions"]))
        st["step"] = int(data["step"])
        _flows[flow] = st
        return _view(flow, st)


def copy_flow(src, dst):
    """把 src 批次的条件复制到新批次 dst。"""
    return import_flow(dst, export(src))
