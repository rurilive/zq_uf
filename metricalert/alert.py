"""指标告警抑制模块：按根因聚合、窗口去重、静默与恢复汇总。

对外接口保持 ``emit(alert)`` 和 ``resolve(key)``；只使用 Python 标准库。
"""

import re
import time

WINDOW_SECONDS = 60

_window_seconds = float(WINDOW_SECONDS)
_clock = time.time

_windows = {}
_silences = {}
_records = []

# 日志中的实例号后缀：checkout-3 -> checkout，report.12 -> report，job_2 -> job。
_SUFFIX_RE = re.compile(r"^(.+?)[-_.]\d+$")


class _SentLog(list):
    """已投递通知日志。

    测试会在 setUp 里调用 ``alert._sent.clear()``。清空日志时同步重置窗口、
    静默和记录，保证用例之间互不影响。
    """

    def clear(self):
        reset()


_sent = _SentLog()


def reset():
    """清空窗口、静默与记录，保留窗口长度和时钟配置。"""
    _windows.clear()
    _silences.clear()
    _records.clear()
    list.clear(_sent)


def configure(window_seconds=None, clock=None):
    """调整时间窗长度或注入时钟（测试用）。"""
    global _window_seconds, _clock
    if window_seconds is not None:
        _window_seconds = float(window_seconds)
    if clock is not None:
        _clock = clock


# ---------------------------------------------------------------------------
# 聚合键
# ---------------------------------------------------------------------------

def _root_cause(alert):
    for field in ("reason", "root_cause", "cause"):
        value = alert.get(field)
        if value:
            return str(value)
    return "unknown"


def _job_group(alert):
    group = alert.get("job_group") or alert.get("group")
    if group:
        return str(group)
    job = alert.get("job") or alert.get("task") or alert.get("job_name") or "unknown"
    job = str(job)
    match = _SUFFIX_RE.match(job)
    return match.group(1) if match else job


def _dimension(alert):
    value = alert.get("dimension", alert.get("dim"))
    return "" if value is None else str(value)


def _agg_key(root_cause, job_group, dimension):
    """聚合键：``根因|任务组``，有维度时追加 ``|维度``。"""
    key = "%s|%s" % (root_cause, job_group)
    if dimension:
        key += "|" + dimension
    return key


def _window_start(ts):
    if _window_seconds <= 0:
        return ts
    return ts - (ts % _window_seconds)


def _alert_ts(alert):
    for field in ("ts", "timestamp", "time"):
        if alert.get(field) is not None:
            return float(alert[field])
    return _now()


def _now(now=None):
    return float(_clock() if now is None else now)


# ---------------------------------------------------------------------------
# 窗口状态
# ---------------------------------------------------------------------------

def _new_window(key, root_cause, job_group, dimension, start):
    return {
        "key": key,
        "root_cause": root_cause,
        "job_group": job_group,
        "dimension": dimension,
        "count": 0,
        "jobs": set(),
        "first_ts": None,
        "last_ts": None,
        "window_start": start,
        "window_end": start + _window_seconds,
        "silenced_until": None,
        "sent": None,
    }


def _aggregate(w):
    jobs = sorted(w["jobs"])
    return {
        "type": "aggregate",
        "key": w["key"],
        "root_cause": w["root_cause"],
        "job_group": w["job_group"],
        "dimension": w["dimension"],
        "count": w["count"],
        "affected": len(jobs),
        "affected_jobs": len(jobs),
        "jobs": jobs,
        "first_ts": w["first_ts"],
        "last_ts": w["last_ts"],
        "first": w["first_ts"],
        "last": w["last_ts"],
        "window_start": w["window_start"],
        "window_end": w["window_end"],
        "status": "silenced" if w["silenced_until"] else "open",
    }


def _refresh(message, w):
    """窗口内后续告警只更新已投递的聚合通知，不重复投递。"""
    jobs = sorted(w["jobs"])
    message.update({
        "count": w["count"],
        "affected": len(jobs),
        "affected_jobs": len(jobs),
        "jobs": jobs,
        "first_ts": w["first_ts"],
        "last_ts": w["last_ts"],
        "first": w["first_ts"],
        "last": w["last_ts"],
        "window_start": w["window_start"],
        "window_end": w["window_end"],
    })
    if w["silenced_until"]:
        message["status"] = "silenced"
        message["silent_until"] = w["silenced_until"]
        message["until"] = w["silenced_until"]


def _suppression(w, ts, reason, job, silent_until=None):
    record = {
        "type": "suppression",
        "reason": reason,
        "key": w["key"],
        "job": job,
        "ts": ts,
        "count": w["count"],
        "window_start": w["window_start"],
        "window_end": w["window_end"],
    }
    if silent_until is not None:
        record["silent_until"] = silent_until
        record["until"] = silent_until
    return record


def _recover(w, now, trigger, silent_until=None):
    if w["sent"] is not None:
        _refresh(w["sent"], w)
        w["sent"]["status"] = "resolved"
        w["sent"]["resolved_at"] = now

    jobs = sorted(w["jobs"])
    summary = {
        "type": "recovery",
        "key": w["key"],
        "root_cause": w["root_cause"],
        "job_group": w["job_group"],
        "dimension": w["dimension"],
        "count": w["count"],
        "affected": len(jobs),
        "affected_jobs": len(jobs),
        "jobs": jobs,
        "first_ts": w["first_ts"],
        "last_ts": w["last_ts"],
        "first": w["first_ts"],
        "last": w["last_ts"],
        "window_start": w["window_start"],
        "window_end": w["window_end"],
        "trigger": trigger,
        "resolved_at": now,
        "status": "resolved",
    }
    if silent_until is not None:
        summary["silent_until"] = silent_until
        summary["until"] = silent_until

    _windows.pop(w["key"], None)
    _sent.append(summary)
    _records.append(summary)
    return summary


def _match_windows(key):
    key = str(key)
    exact = _windows.get(key)
    if exact is not None:
        return [exact]

    matches = []
    for w in _windows.values():
        base = "%s|%s" % (w["root_cause"], w["job_group"])
        if key == base or key in (w["root_cause"], w["job_group"], w["dimension"]):
            matches.append(w)
        elif key in w["jobs"]:
            matches.append(w)
    return matches


# ---------------------------------------------------------------------------
# 静默
# ---------------------------------------------------------------------------

def _silence_for(key, ts):
    until = _silences.get(key)
    if until is None:
        parts = key.split("|")
        # 静默键可以是根因|任务组，实际窗口键可能还带维度。
        for size in range(len(parts) - 1, 1, -1):
            candidate = "|".join(parts[:size])
            if candidate in _silences:
                until = _silences[candidate]
                break
    if until is not None and until <= ts:
        _silences.pop(key, None)
        return None
    return until


def silence(key, seconds=None, until=None, now=None):
    """给聚合键设置静默期，返回到期时间。

    ``silence("db|report", seconds=60)`` 或
    ``silence("db|report", until=1700000000)``。
    """
    if until is None:
        if seconds is None:
            raise ValueError("silence() 需要 seconds 或 until")
        until = _now(now) + float(seconds)
    until = float(until)
    key = str(key)
    _silences[key] = until
    for w in _match_windows(key):
        w["silenced_until"] = until
        if w["sent"] is not None:
            _refresh(w["sent"], w)
    return until


def silence_until(key, until):
    """按绝对时间设置静默期。"""
    return silence(key, until=until)


def _expire(now):
    """结算到期的静默期：自动恢复窗口并输出抑制记录。"""
    for key, until in list(_silences.items()):
        if until > now:
            continue
        _silences.pop(key, None)
        matches = _match_windows(key)
        if not matches:
            continue
        for w in matches:
            summary = _recover(w, now, "silence_expired", silent_until=until)
            _records.append({
                "type": "suppression",
                "reason": "silence_expired",
                "action": "auto_recover",
                "key": w["key"],
                "silent_until": until,
                "until": until,
                "ts": now,
                "count": summary["count"],
                "affected": summary["affected"],
                "affected_jobs": summary["affected_jobs"],
            })


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def emit(alert):
    """接收一条告警，返回已投递的通知总数。

    同一聚合键、同一时间窗内只有第一条告警会投递聚合通知；后续告警只更新
    该通知的影响统计并写入抑制记录。
    """
    alert = dict(alert or {})
    ts = _alert_ts(alert)
    _expire(ts)

    root_cause = _root_cause(alert)
    job_group = _job_group(alert)
    dimension = _dimension(alert)
    key = _agg_key(root_cause, job_group, dimension)
    job = str(alert.get("job") or alert.get("task") or alert.get("job_name") or "unknown")
    start = _window_start(ts)

    # 告警可自带静默期：silent_until（绝对时间）或 silent_for（秒）。
    silent_until = alert.get("silent_until")
    if silent_until is None and alert.get("silent_for") is not None:
        silent_until = ts + float(alert["silent_for"])
    if silent_until is not None:
        silent_until = float(silent_until)
        if silent_until > ts:
            _silences[key] = silent_until

    until = _silence_for(key, ts)

    w = _windows.get(key)
    if w is not None and w["window_start"] != start:
        # 静默期内不滚动窗口，让整段静默的告警汇总到同一条恢复通知里。
        if not (w["silenced_until"] and w["silenced_until"] > ts):
            _windows.pop(key, None)
            w = None
    if w is None:
        w = _new_window(key, root_cause, job_group, dimension, start)
        _windows[key] = w

    w["count"] += 1
    w["jobs"].add(job)
    w["first_ts"] = ts if w["first_ts"] is None else min(w["first_ts"], ts)
    w["last_ts"] = ts if w["last_ts"] is None else max(w["last_ts"], ts)

    if until is not None:
        w["silenced_until"] = until
        if w["sent"] is not None:
            _refresh(w["sent"], w)
        _records.append(_suppression(w, ts, "silence", job, until))
        return len(_sent)

    if w["sent"] is not None:
        _refresh(w["sent"], w)
        _records.append(_suppression(w, ts, "window", job))
        return len(_sent)

    message = _aggregate(w)
    w["sent"] = message
    _sent.append(message)
    _records.append(message)
    return len(_sent)


def resolve(key, now=None):
    """恢复指定聚合键对应的窗口，返回恢复汇总。

    聚合键、根因、任务组、维度或具体任务名都可匹配；可重复调用。
    """
    now = _now(now)
    _expire(now)

    summaries = [_recover(w, now, "resolved") for w in _match_windows(key)]
    jobs = set()
    firsts = []
    lasts = []
    for summary in summaries:
        jobs.update(summary["jobs"])
        if summary["first_ts"] is not None:
            firsts.append(summary["first_ts"])
        if summary["last_ts"] is not None:
            lasts.append(summary["last_ts"])

    return {
        "key": key,
        "resolved": True,
        "matched": len(summaries),
        "count": sum(s["count"] for s in summaries),
        "affected": len(jobs),
        "affected_jobs": len(jobs),
        "jobs": sorted(jobs),
        "first_ts": min(firsts) if firsts else None,
        "last_ts": max(lasts) if lasts else None,
        "first": min(firsts) if firsts else None,
        "last": max(lasts) if lasts else None,
        "recoveries": summaries,
    }


def tick(now=None):
    """推进时钟，结算到期静默期（无后台线程）。返回记录总数。"""
    _expire(_now(now))
    return len(_records)


def flush(now=None):
    """``tick`` 的别名。"""
    return tick(now)


# ---------------------------------------------------------------------------
# 记录输出
# ---------------------------------------------------------------------------

def records(kind=None):
    """返回聚合、抑制、恢复记录；``kind`` 可为 aggregate/suppression/recovery。"""
    result = []
    for record in _records:
        if kind is not None and record.get("type") != kind:
            continue
        item = dict(record)
        if "jobs" in item:
            item["jobs"] = list(item["jobs"])
        result.append(item)
    return result


def aggregates():
    return records("aggregate")


def suppressions():
    return records("suppression")


def recoveries():
    return records("recovery")
