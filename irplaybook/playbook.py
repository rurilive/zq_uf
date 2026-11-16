"""按事件类型编排的应急处置清单。

规则：
- 每类事件登记带前置条件（pre）的步骤，同 group 的步骤标注为可并行；
- 执行只登记操作人、时间、结果，不自动推进任何步骤；
- 前置步骤未完成不得执行，取证（forensic=True）步骤因此天然先于清理步骤；
- 步骤失败或超时即停在该步骤并登记遗留项，后续步骤保持 blocked；
- 遗留项须重试成功（自动核销）或由人工逐项确认；
- 关闭前校验必做项是否完成、遗留项是否全部确认/核销；
- 关闭后的事件可按类型统计处置耗时。
"""

from datetime import datetime

# 每个步骤：
#   name     步骤名
#   pre      前置步骤，全部 done 后本步骤才可执行
#   required 必做项，关闭前必须 done（可选步骤失败可凭遗留项确认关闭）
#   forensic 取证步骤，清理类步骤必须把它列在 pre 中
#   group    并行组标识，同组步骤互为 parallel_with，互不阻断
_TEMPLATES = {
    "ransomware": [
        {"name": "isolate", "pre": [], "required": True, "forensic": False},
        {"name": "collect", "pre": ["isolate"], "required": True, "forensic": True},
        {"name": "cleanup", "pre": ["collect"], "required": True, "forensic": False},
        {"name": "restore", "pre": ["cleanup"], "required": True, "forensic": False},
    ],
    "phishing": [
        {"name": "quarantine", "pre": [], "required": True, "forensic": False},
        {"name": "collect", "pre": ["quarantine"], "required": True,
         "forensic": True, "group": "g1"},
        {"name": "notify", "pre": ["quarantine"], "required": True,
         "forensic": False, "group": "g1"},
    ],
    "intrusion": [
        {"name": "isolate", "pre": [], "required": True, "forensic": False},
        {"name": "collect", "pre": ["isolate"], "required": True, "forensic": True},
        {"name": "cleanup", "pre": ["collect"], "required": True, "forensic": False},
        {"name": "review", "pre": ["cleanup"], "required": True, "forensic": False},
        # 可选加固项：做不完时允许凭遗留项确认走关闭流程
        {"name": "harden", "pre": ["review"], "required": False, "forensic": False},
    ],
}

_EVENTS = {}
_clock = datetime.now

_RESULTS = ("done", "failed", "timeout")


def set_clock(func):
    """注入时钟（测试/演示用），func 无参并返回 datetime。"""
    global _clock
    _clock = func


def reset():
    """清空已开单的事件。"""
    _EVENTS.clear()


def _now():
    return _clock()


def _iso(value):
    return value.isoformat() if value is not None else None


def _get(event_id):
    event = _EVENTS.get(event_id)
    if event is None:
        raise ValueError("unknown event")
    return event


def _defs(event):
    return _TEMPLATES[event["type"]]


def _def(event, step_name):
    for definition in _defs(event):
        if definition["name"] == step_name:
            return definition
    raise ValueError("unknown step")


def _record(event, kind, operator, step=None, result=None, note=""):
    event["seq"] += 1
    # seq 保证同一时刻产生的记录也有稳定的时间顺序
    return {
        "seq": event["seq"],
        "at": _now(),
        "kind": kind,
        "operator": operator,
        "step": step,
        "result": result,
        "note": note,
    }


def _runnable(event, step_name):
    """返回 (是否可执行, 原因)。同并行组的对等步骤互不阻断。"""
    if event["state"] != "open":
        return False, "event already closed"
    state = event["steps"][step_name]
    if state["state"] == "done":
        return False, "step already done"
    definition = _def(event, step_name)
    missing = [p for p in definition["pre"]
               if event["steps"][p]["state"] != "done"]
    if missing:
        return False, "precondition not met: %s not done" % ", ".join(missing)
    return True, ""


def open(event):
    """按事件类型取步骤模板开单，返回步骤状态汇总。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get("id")
    kind = event.get("type")
    if not event_id or not kind:
        raise ValueError("event needs id and type")
    if kind not in _TEMPLATES:
        raise ValueError("unknown event type")
    if event_id in _EVENTS:
        raise ValueError("event already exists")

    operator = event.get("operator", "")
    instance = {
        "id": event_id,
        "type": kind,
        "state": "open",
        "opened_at": _now(),
        "closed_at": None,
        "steps": {
            d["name"]: {
                "state": "pending",
                "attempts": 0,
                "operators": [],
                "updated_at": None,
            }
            for d in _TEMPLATES[kind]
        },
        "records": [],
        "leftovers": {},
        "seq": 0,
    }
    _EVENTS[event_id] = instance
    instance["records"].append(_record(instance, "open", operator))
    return {
        "id": event_id,
        "type": kind,
        "state": "open",
        "steps": len(instance["steps"]),
        "done": 0,
    }


def run(event_id, step, operator, result, note=""):
    """执行一个步骤并记录操作人、时间、结果。

    result: done / failed / timeout。失败或超时会登记遗留项并阻断后续步骤；
    前置未满足、事件已关闭或步骤已完成时抛 ValueError。
    """
    event = _get(event_id)
    if event["state"] != "open":
        raise ValueError("event already closed")
    if step not in event["steps"]:
        raise ValueError("unknown step")
    if result not in _RESULTS:
        raise ValueError("result must be one of %s" % ", ".join(_RESULTS))

    ok, reason = _runnable(event, step)
    if not ok:
        raise ValueError(reason)

    state = event["steps"][step]
    state["attempts"] += 1
    if operator not in state["operators"]:
        state["operators"].append(operator)
    state["updated_at"] = _now()
    state["state"] = "done" if result == "done" else result

    record = _record(event, "step", operator, step=step, result=result, note=note)
    event["records"].append(record)

    if result == "done":
        leftover = event["leftovers"].get(step)
        if leftover is not None and not leftover["resolved"]:
            # 重试成功，遗留项自动核销
            leftover["resolved"] = "retried"
            leftover["resolved_at"] = _now()
    else:
        # 再次失败会重新打开该步骤的遗留项，旧确认随之失效
        event["leftovers"][step] = {
            "step": step,
            "reason": result,
            "at": _now(),
            "operator": operator,
            "note": note,
            "confirmed": False,
            "confirmed_by": None,
            "confirmed_at": None,
            "resolve_note": None,
            "resolved": None,
            "resolved_at": None,
        }

    return {"step": step, "state": state["state"], "seq": record["seq"]}


def confirm_leftover(event_id, step, confirmer, note):
    """逐项确认遗留项，记录确认人、时间和说明。"""
    event = _get(event_id)
    leftover = event["leftovers"].get(step)
    if leftover is None:
        raise ValueError("no leftover for step %s" % step)
    if leftover["resolved"]:
        raise ValueError("leftover already resolved")
    if leftover["confirmed"]:
        raise ValueError("leftover already confirmed")
    leftover["confirmed"] = True
    leftover["confirmed_by"] = confirmer
    leftover["confirmed_at"] = _now()
    leftover["resolve_note"] = note
    event["records"].append(
        _record(event, "leftover_confirm", confirmer, step=step, note=note)
    )
    return {"step": step, "confirmed": True}


def _close_check(event):
    """关闭检查：逐项给出必做项/可选项的满足情况。"""
    items = []
    for definition in _defs(event):
        name = definition["name"]
        state = event["steps"][name]["state"]
        leftover = event["leftovers"].get(name)
        acknowledged = bool(leftover and (leftover["confirmed"] or leftover["resolved"]))

        if state == "done":
            satisfied, detail = True, "done"
        elif definition["required"]:
            # 必做项即使确认了遗留项也仍须补做完成
            satisfied = False
            detail = "%s: mandatory step incomplete" % state
        else:
            satisfied = acknowledged
            detail = "leftover confirmed" if acknowledged else \
                "%s: leftover not confirmed" % state

        items.append({
            "step": name,
            "required": definition["required"],
            "state": state,
            "satisfied": satisfied,
            "detail": detail,
        })
    return items


def _unconfirmed_leftovers(event):
    return [name for name, item in event["leftovers"].items()
            if not item["confirmed"] and not item["resolved"]]


def closable(event):
    """关闭双门禁：必做项全完成，且遗留项全部确认或核销。"""
    if event["state"] != "open":
        return False
    check = _close_check(event)
    return all(item["satisfied"] for item in check) and \
        not _unconfirmed_leftovers(event)


def close(event_id, operator):
    """关闭事件；门禁不通过时抛 ValueError 并附明原因。"""
    event = _get(event_id)
    if event["state"] == "closed":
        raise ValueError("event already closed")

    check = _close_check(event)
    incomplete = [item["step"] for item in check if not item["satisfied"]]
    pending_leftovers = _unconfirmed_leftovers(event)
    problems = []
    if incomplete:
        problems.append("incomplete steps: %s" % ", ".join(incomplete))
    if pending_leftovers:
        problems.append("unconfirmed leftovers: %s" % ", ".join(pending_leftovers))
    if problems:
        raise ValueError("event not closable; " + "; ".join(problems))

    event["state"] = "closed"
    event["closed_at"] = _now()
    event["records"].append(_record(event, "close", operator))
    return timeline(event_id)


def _parallel_peers(event, step_name):
    group = _def(event, step_name).get("group")
    if not group:
        return []
    return [d["name"] for d in _defs(event)
            if d.get("group") == group and d["name"] != step_name]


def _step_view(event, definition):
    name = definition["name"]
    state = event["steps"][name]
    can_run, _ = _runnable(event, name)
    return {
        "name": name,
        "state": state["state"],
        "required": definition["required"],
        "forensic": definition["forensic"],
        "group": definition.get("group"),
        "parallel_with": _parallel_peers(event, name),
        "runnable": can_run,
        "blocked": state["state"] == "pending" and not can_run
                   and event["state"] == "open",
        "attempts": state["attempts"],
        "operator": state["operators"][-1] if state["operators"] else None,
        "operators": list(state["operators"]),
        "updated_at": _iso(state["updated_at"]),
    }


def _leftover_view(item):
    return {
        "step": item["step"],
        "reason": item["reason"],
        "at": _iso(item["at"]),
        "operator": item["operator"],
        "note": item["note"],
        "confirmed": item["confirmed"],
        "confirmed_by": item["confirmed_by"],
        "confirmed_at": _iso(item["confirmed_at"]),
        "resolve_note": item["resolve_note"],
        "resolved": item["resolved"],
        "resolved_at": _iso(item["resolved_at"]),
    }


def timeline(event_id):
    """返回步骤状态、操作记录、遗留项和关闭检查结果。"""
    event = _get(event_id)
    definitions = _defs(event)
    return {
        "id": event_id,
        "type": event["type"],
        "state": event["state"],
        "opened_at": _iso(event["opened_at"]),
        "closed_at": _iso(event["closed_at"]),
        "steps": [_step_view(event, d) for d in definitions],
        "records": [
            dict(record, at=_iso(record["at"]))
            for record in sorted(event["records"], key=lambda r: r["seq"])
        ],
        "leftover": [
            _leftover_view(event["leftovers"][d["name"]])
            for d in definitions if d["name"] in event["leftovers"]
        ],
        "close_check": _close_check(event),
        "closable": closable(event),
    }


def stats(kind=None):
    """按事件类型统计已关闭事件的处置耗时（秒），未关闭事件不计入。"""
    buckets = {}
    for event in _EVENTS.values():
        if event["state"] != "closed":
            continue
        if kind is not None and event["type"] != kind:
            continue
        seconds = (event["closed_at"] - event["opened_at"]).total_seconds()
        buckets.setdefault(event["type"], []).append(seconds)

    def summarize(values):
        return {
            "count": len(values),
            "total_seconds": round(sum(values), 3),
            "avg_seconds": round(sum(values) / len(values), 3),
            "min_seconds": round(min(values), 3),
            "max_seconds": round(max(values), 3),
        }

    result = {name: summarize(values) for name, values in buckets.items()}
    if kind is not None:
        return result.get(kind, {})
    return result
