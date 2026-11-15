# -*- coding: utf-8 -*-
"""多步采集表单：按采集批次保存每一步的条件与令牌。
规则：
- 每个批次（flow）独立保存步骤条件、令牌和结果，批次间互不影响；
- 条件随提交随保存；回退到某一步时该步及其后的条件作废并重新获取页面状态；
- 令牌单次有效：重复提交同一令牌直接返回首次响应（replayed=True），
  不会产生第二套结果；
- 条件校验失败不消耗令牌、不清空已选条件，修复后可从原步骤继续；
- 批次条件可导出为纯数据，导入/复制到新批次后从原步骤继续，且不覆盖
  已有批次。

响应统一包含：
- step：当前所在步骤（正在填写的步骤，1 起）；
- rolled_back / replayed：本次调用是否为回退 / 重复提交；
- 失败时额外带 error 与 field，指明失败步骤（step）和条件字段（field）。
"""
import threading

# 站点检索页的四步：行业 -> 地区 -> 时间范围 -> 翻页抓取
DEFAULT_STEPS = ("industry", "region", "time_range", "crawl")

_flows = {}
# 同一批次并发重复提交时，靠这把锁串行化，保证令牌检查与状态推进是原子的：
# 先到的提交成功，其余全部命中重放缓存，结果集只产生一套。
_lock = threading.RLock()


def reset():
    """清空全部批次（主要供测试使用）。"""
    with _lock:
        _flows.clear()


def _make_token(flow, completed, revision):
    """令牌后缀等于已完成的步骤数；回退后 revision 递增，旧令牌立即失效。"""
    if revision == 0:
        return "%s-%d" % (flow, completed)
    return "%s-%d-r%d" % (flow, completed, revision)


def _open_step(st):
    """当前正在填写的步骤：已结束时停在末步，否则为已完成数 + 1。"""
    if st["finished"]:
        return st["completed"]
    return st["completed"] + 1


def _merged_state(st):
    """汇总各步已保存条件的最新快照（随回退/导入只保留仍有效的步骤）。"""
    merged = {}
    for key in sorted(st["conditions"]):
        merged.update(st["conditions"][key])
    return merged


def _snapshot_conditions(st):
    return {key: dict(value) for key, value in sorted(st["conditions"].items())}


def _page(st):
    """页面状态：generation 每提交一步或回退重取时递增。"""
    return {"step": _open_step(st), "generation": st["generation"],
            "token": st["token"]}


def _response(st, flow, rolled_back=False, replayed=False, result=None):
    resp = {
        "ok": True,
        "flow": flow,
        "step": _open_step(st),
        "token": st["token"],
        "state": _merged_state(st),
        "conditions": _snapshot_conditions(st),
        "page": _page(st),
        "replayed": replayed,
        "rolled_back": rolled_back,
        "finished": st["finished"],
    }
    if result is not None:
        resp["result"] = result
    return resp


def _failure(flow, error, st=None, field=None):
    if st is None:
        return {"ok": False, "flow": flow, "step": None, "token": None,
                "state": {}, "conditions": {}, "page": None,
                "error": error, "field": field,
                "replayed": False, "rolled_back": False, "finished": False}
    return {
        "ok": False,
        "flow": flow,
        "step": _open_step(st),
        "token": st["token"],
        "state": _merged_state(st),
        "conditions": _snapshot_conditions(st),
        "page": _page(st),
        "error": error,
        "field": field,
        "replayed": False,
        "rolled_back": False,
        "finished": st["finished"],
    }


def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) == 0
    return False


def _build_result(flow, st):
    """末步（翻页抓取）成功后只构建一次结果集。"""
    schema = st["schema"]
    crawl = st["conditions"][len(schema)].get("crawl")
    pages = crawl if isinstance(crawl, int) and crawl > 0 else 1
    return {
        "batch": flow,
        "pages": pages,
        "conditions": _merged_state(st),
        "rows": [{"id": "%s-row-%d" % (flow, i)} for i in range(1, pages + 1)],
    }


def begin(flow, steps=None):
    """新建采集批次并发放第一步令牌。

    steps 给出每一步要求的条件字段名（默认 DEFAULT_STEPS 的四步采集流程
    通过显式传入启用）；缺省为不做字段校验的通用模式，兼容旧用法。
    """
    with _lock:
        st = {
            "completed": 0,
            "revision": 0,
            "generation": 1,
            "token": _make_token(flow, 0, 0),
            "schema": tuple(steps) if steps else None,
            "conditions": {},
            "consumed": {},
            "result": None,
            "finished": False,
            "last_failure": None,
        }
        _flows[flow] = st
        return _response(st, flow)


def step(flow, data, token=None):
    """提交当前步骤的条件。

    - 携带已消耗的令牌：判定为重复提交，原样返回首次响应，replayed=True；
    - 携带与当前令牌不符的令牌：stale_token 失败，状态不变；
    - 字段缺失/为空：失败并指明 step 与 field，令牌不消耗、条件不清除，
      修复后用同一令牌从原步骤重试即可。
    """
    with _lock:
        st = _flows.get(flow)
        if st is None:
            return _failure(flow, "unknown_flow")

        if token is not None:
            if token in st["consumed"]:
                return dict(st["consumed"][token], replayed=True)
            if token != st["token"]:
                return _failure(flow, "stale_token", st=st)

        if st["finished"]:
            return _failure(flow, "already_finished", st=st)

        if not isinstance(data, dict):
            st["last_failure"] = {"step": _open_step(st), "field": None,
                                  "error": "invalid_data"}
            return _failure(flow, "invalid_data", st=st)

        open_step = _open_step(st)
        field = st["schema"][open_step - 1] if st["schema"] else None
        if field is not None:
            if field not in data:
                st["last_failure"] = {"step": open_step, "field": field,
                                      "error": "missing_condition"}
                return _failure(flow, "missing_condition", st=st, field=field)
            if _is_empty(data[field]):
                st["last_failure"] = {"step": open_step, "field": field,
                                      "error": "empty_condition"}
                return _failure(flow, "empty_condition", st=st, field=field)

        used_token = st["token"]
        st["conditions"][open_step] = dict(data)
        st["completed"] = open_step
        st["generation"] += 1

        result = None
        if st["schema"] is not None and open_step == len(st["schema"]):
            st["finished"] = True
            if st["result"] is None:
                st["result"] = _build_result(flow, st)
            result = st["result"]

        st["token"] = _make_token(flow, st["completed"], st["revision"])
        resp = _response(st, flow, result=result)
        st["consumed"][used_token] = resp
        return resp


def back(flow, to):
    """回退到第 to 步（数字或条件字段名）。

    该步及其之后的条件全部作废（该步成为待重填的当前步），该步之前
    的条件保留；页面状态重新获取（generation 递增、换发新令牌），
    旧令牌一律失效。
    """
    with _lock:
        st = _flows.get(flow)
        if st is None:
            return _failure(flow, "unknown_flow")

        if isinstance(to, str):
            if not st["schema"] or to not in st["schema"]:
                return _failure(flow, "invalid_step", st=st)
            to = st["schema"].index(to) + 1

        bound = st["completed"] if st["finished"] else st["completed"] + 1
        if not isinstance(to, int) or not (1 <= to <= bound):
            return _failure(flow, "invalid_step", st=st)

        # 第 to 步是待重填的当前步，其原值与其后各步条件一并作废；
        # 只保留第 to 步之前已确认的条件。
        for key in list(st["conditions"]):
            if key >= to:
                del st["conditions"][key]

        st["completed"] = to - 1
        st["finished"] = False
        st["result"] = None
        st["consumed"] = {}
        st["revision"] += 1
        st["generation"] += 1
        st["token"] = _make_token(flow, st["completed"], st["revision"])
        st["last_failure"] = None
        return _response(st, flow, rolled_back=True)


def export_conditions(flow):
    """导出批次条件为可 JSON 序列化的纯数据（不含结果集）。"""
    with _lock:
        st = _flows.get(flow)
        if st is None:
            return _failure(flow, "unknown_flow")
        return {
            "format": "formstep-conditions/1",
            "flow": flow,
            "schema": list(st["schema"]) if st["schema"] else None,
            "completed": st["completed"],
            "step": _open_step(st),
            "finished": st["finished"],
            "conditions": {str(k): dict(v)
                           for k, v in sorted(st["conditions"].items())},
        }


def import_conditions(payload, flow):
    """把导出的条件导入为新批次，从原步骤继续；目标批次已存在则拒绝覆盖。

    已结束批次的末步结果作废，导入后停在末步等待重新抓取；未结束批次
    停在导出时正在填写的步骤。其他批次不受影响。
    """
    if not isinstance(payload, dict) or \
            payload.get("format") != "formstep-conditions/1":
        raise ValueError("invalid formstep conditions payload")
    with _lock:
        if flow in _flows:
            return _failure(flow, "flow_exists")

        schema = payload.get("schema")
        conditions = {int(k): dict(v)
                      for k, v in payload.get("conditions", {}).items()}
        saved = int(payload.get("completed", len(conditions)))
        # 已结束：末步结果作废、回到末步重抓；未结束：停在导出时正在填写的步骤
        if payload.get("finished"):
            conditions = {k: v for k, v in conditions.items() if k < saved}
            completed = max(saved - 1, 0)
        else:
            completed = saved

        st = {
            "completed": completed,
            "revision": 0,
            "generation": 1,
            "token": _make_token(flow, completed, 0),
            "schema": tuple(schema) if schema else None,
            "conditions": conditions,
            "consumed": {},
            "result": None,
            "finished": False,
            "last_failure": None,
        }
        _flows[flow] = st
        return _response(st, flow)


def copy_flow(src, dst):
    """把源批次的条件复制为新批次；源批次与其他批次均不受影响。"""
    with _lock:  # RLock：export/import 在内部还要各取一次锁
        payload = export_conditions(src)
        if payload.get("format") != "formstep-conditions/1":
            return payload
        return import_conditions(payload, dst)
