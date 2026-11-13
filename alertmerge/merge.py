"""数据任务告警聚合：按根因、任务组和时间窗去重，恢复发汇总。"""

import time

_VALID_STATES = ("firing", "recovered")

_sent = []

_config = {}

_incidents = {}
_tombstones = {}
_silences = {}


def reset():
    _sent.clear()
    _incidents.clear()
    _tombstones.clear()
    _silences.clear()
    _config.clear()
    _config.update({"window_seconds": 0.0, "escalate_after": None})


reset()


def configure(window_seconds=0.0, escalate_after=None):
    """设置聚合窗口（秒）和升级阈值（同一窗口内合并条数达到即升级一次）。"""
    if not _is_number(window_seconds) or window_seconds < 0:
        raise ValueError("window_seconds 必须是非负数字")
    if escalate_after is not None:
        if not isinstance(escalate_after, int) or escalate_after < 2:
            raise ValueError("escalate_after 必须是 >= 2 的整数或 None")
    _config["window_seconds"] = float(window_seconds)
    _config["escalate_after"] = escalate_after


def silence(reason, group, until):
    """静默指定聚合键到 until（epoch 秒），期间 firing 不发送但仍统计。"""
    if not _is_number(until):
        raise ValueError("until 必须是 epoch 秒数字")
    _silences[(reason, group)] = float(until)


def emit(alert):
    """接收一条告警；窗口关闭时逐条透传，窗口开启时按 (reason, group) 聚合。"""
    if _config["window_seconds"] <= 0:
        row = dict(alert)
        _sent.append(row)
        return len(_sent)
    job, state, ts, reason, group = _validate(alert)
    if state == "firing":
        _emit_firing(job, ts, reason, group)
    else:
        _emit_recovered(ts, reason, group)
    return len(_sent)


def sent():
    return list(_sent)


def dump_state():
    """导出可 JSON 序列化的聚合状态，便于重启后复现。"""
    return {
        "config": dict(_config),
        "incidents": [
            {
                "reason": key[0],
                "group": key[1],
                "window_start": inc["window_start"],
                "jobs": sorted(inc["jobs"]),
                "first": inc["first"],
                "last": inc["last"],
                "count": inc["count"],
                "escalated": inc["escalated"],
            }
            for key, inc in sorted(_incidents.items())
        ],
        "tombstones": [
            {"reason": key[0], "group": key[1], "until": until}
            for key, until in sorted(_tombstones.items())
        ],
        "silences": [
            {"reason": key[0], "group": key[1], "until": until}
            for key, until in sorted(_silences.items())
        ],
    }


def load_state(state):
    """从 dump_state 的快照恢复聚合状态，结构不合法时抛 ValueError。"""
    if not isinstance(state, dict):
        raise ValueError("state 必须是 dump_state 返回的字典")
    try:
        config = state["config"]
        incidents = state["incidents"]
        tombstones = state["tombstones"]
        silences = state["silences"]
    except (KeyError, TypeError):
        raise ValueError("state 缺少 config/incidents/tombstones/silences 字段")
    configure(
        window_seconds=config.get("window_seconds", 0.0),
        escalate_after=config.get("escalate_after"),
    )
    _incidents.clear()
    _tombstones.clear()
    _silences.clear()
    for inc in incidents:
        key = _state_key(inc)
        jobs = inc.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("incident.jobs 必须是列表")
        _incidents[key] = {
            "window_start": _state_number(inc, "window_start"),
            "jobs": set(jobs),
            "first": _state_number(inc, "first"),
            "last": _state_number(inc, "last"),
            "count": _state_number(inc, "count"),
            "escalated": bool(inc.get("escalated", False)),
        }
    for entry in tombstones:
        _tombstones[_state_key(entry)] = _state_number(entry, "until")
    for entry in silences:
        _silences[_state_key(entry)] = _state_number(entry, "until")


def _emit_firing(job, ts, reason, group):
    key = (reason, group)
    window = _config["window_seconds"]
    inc = _incidents.get(key)
    if inc is None or ts >= inc["window_start"] + window:
        _tombstones.pop(key, None)
        _incidents[key] = {
            "window_start": ts,
            "jobs": {job},
            "first": ts,
            "last": ts,
            "count": 1,
            "escalated": False,
        }
        if not _silenced(key, ts):
            _sent.append(_row(key, "firing", 1, ts, ts))
        return
    inc["jobs"].add(job)
    inc["first"] = min(inc["first"], ts)
    inc["last"] = max(inc["last"], ts)
    inc["count"] += 1
    threshold = _config["escalate_after"]
    if (
        threshold is not None
        and inc["count"] >= threshold
        and not inc["escalated"]
        and not _silenced(key, ts)
    ):
        inc["escalated"] = True
        _sent.append(_row(key, "escalated", len(inc["jobs"]), inc["first"], inc["last"]))


def _emit_recovered(ts, reason, group):
    key = (reason, group)
    inc = _incidents.pop(key, None)
    if inc is None:
        until = _tombstones.get(key)
        if until is not None and ts <= until:
            return
        raise ValueError(f"recovered 告警没有对应的 firing 事件: reason={reason!r} group={group!r}")
    last = max(inc["last"], ts)
    _sent.append(_row(key, "recovered", len(inc["jobs"]), inc["first"], last))
    _tombstones[key] = ts + _config["window_seconds"]


def _row(key, state, jobs, first, last):
    return {
        "reason": key[0],
        "group": key[1],
        "state": state,
        "jobs": jobs,
        "first": first,
        "last": last,
    }


def _silenced(key, ts):
    until = _silences.get(key)
    if until is None:
        return False
    if ts < until:
        return True
    del _silences[key]
    return False


def _validate(alert):
    if not isinstance(alert, dict):
        raise ValueError("alert 必须是字典")
    job = alert.get("job")
    if not job:
        raise ValueError("alert 缺少 job 字段")
    state = alert.get("state", "firing")
    if state not in _VALID_STATES:
        raise ValueError(f"未知 state: {state!r}，只支持 {_VALID_STATES}")
    ts = alert.get("time", time.time())
    if not _is_number(ts):
        raise ValueError("time 必须是 epoch 秒数字")
    reason = alert.get("reason", "unknown")
    group = alert.get("group", "default")
    return job, state, float(ts), reason, group


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _state_key(entry):
    try:
        return (entry["reason"], entry["group"])
    except (KeyError, TypeError):
        raise ValueError("状态条目缺少 reason/group 字段")


def _state_number(entry, field):
    value = entry.get(field)
    if not _is_number(value):
        raise ValueError(f"状态字段 {field} 必须是数字")
    return float(value) if field != "count" else int(value)
