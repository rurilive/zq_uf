"""多信号加权投票的站点熔断与探针恢复模块。

状态迁移：closed -> open（票数过阈值）-> half_open（冷却后放探针）
-> closed（连续探针成功）；half_open 收到负面信号则退回 open。
"""

import time

SIGNAL_WEIGHTS = {
    "captcha": 3,
    "403": 4,
    "empty": 2,
    "redirect": 1,
    "ratelimit": 2,
}
SUCCESS_SIGNAL = "200"

TRIP_THRESHOLD = 4
VOTE_WINDOW = 300
COOLDOWN = 60
PROBE_INTERVAL = 30
PROBE_SUCCESS_REQUIRED = 3

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

_blocked = {}
_events = []
_listener = None
_now = time.monotonic


def set_clock(func=None):
    """注入时钟函数，便于测试；不传则恢复单调时钟。"""
    global _now
    _now = func if func is not None else time.monotonic


def set_listener(func=None):
    """设置事件回调，接收 (event_type, site, payload)；不传则取消。"""
    global _listener
    _listener = func


def reset():
    """清空全部站点状态、白名单与事件日志。"""
    _blocked.clear()
    del _events[:]


def _emit(event_type, site, payload):
    event = {"type": event_type, "site": site, "time": _now(), **payload}
    _events.append(event)
    if _listener is not None:
        _listener(event_type, site, payload)


def _record(site):
    return _blocked.setdefault(site, {
        "state": CLOSED,
        "votes": [],
        "opened_at": None,
        "last_probe_at": None,
        "probe_streak": 0,
        "probes": [],
        "whitelist_until": None,
    })


def whitelist(site, ttl):
    """人工加白，ttl 秒内一律放行；加白立即解除熔断。"""
    rec = _record(site)
    rec["whitelist_until"] = _now() + ttl
    rec["votes"].clear()
    rec["probe_streak"] = 0
    rec["last_probe_at"] = None
    rec["probes"].clear()
    if rec["state"] != CLOSED:
        _transition(rec, site, CLOSED, reason="whitelist")
    _emit("whitelist", site, {"ttl": ttl, "expire_at": rec["whitelist_until"]})


def unwhitelist(site):
    """提前撤销白名单。"""
    rec = _blocked.get(site)
    if rec is not None:
        rec["whitelist_until"] = None
        _emit("unwhitelist", site, {})


def is_whitelisted(site):
    rec = _blocked.get(site)
    if rec is None:
        return False
    until = rec["whitelist_until"]
    if until is None:
        return False
    if until <= _now():
        rec["whitelist_until"] = None
        return False
    return True


def _transition(rec, site, new_state, reason):
    old_state = rec["state"]
    rec["state"] = new_state
    if new_state == OPEN:
        rec["opened_at"] = _now()
    if new_state == CLOSED:
        rec["votes"].clear()
        rec["opened_at"] = None
        rec["last_probe_at"] = None
        rec["probe_streak"] = 0
        rec["probes"].clear()
    _emit("transition", site, {
        "from": old_state,
        "to": new_state,
        "reason": reason,
    })


def _prune(rec):
    cutoff = _now() - VOTE_WINDOW
    rec["votes"] = [vote for vote in rec["votes"] if vote[0] > cutoff]


def tally(site):
    """返回投票窗口内的有效加权票数。"""
    rec = _blocked.get(site)
    if rec is None:
        return 0
    _prune(rec)
    return sum(vote[1] for vote in rec["votes"])


def votes(site):
    """返回窗口内有效票明细 [(time, weight, signal), ...]。"""
    rec = _blocked.get(site)
    if rec is None:
        return []
    _prune(rec)
    return [tuple(vote) for vote in rec["votes"]]


def state(site):
    """返回站点当前状态：closed / open / half_open。"""
    rec = _blocked.get(site)
    return rec["state"] if rec is not None else CLOSED


def probes(site):
    """返回探针结果明细。"""
    rec = _blocked.get(site)
    return list(rec["probes"]) if rec is not None else []


def events(site=None):
    """返回事件日志（票数、状态迁移、探针、白名单），可按站点过滤。"""
    if site is None:
        return list(_events)
    return [event for event in _events if event["site"] == site]


def _maintain(rec, site):
    """open 冷却结束后进入 half_open，等待探针。"""
    if rec["state"] == OPEN and _now() - rec["opened_at"] >= COOLDOWN:
        _transition(rec, site, HALF_OPEN, reason="cooldown")


def observe(site, signal):
    """上报一个信号并按站点加权投票，返回当前是否处于熔断态。"""
    rec = _record(site)
    now = _now()
    white = is_whitelisted(site)
    _maintain(rec, site)

    if signal == SUCCESS_SIGNAL:
        if not white and rec["state"] == HALF_OPEN:
            due = (rec["last_probe_at"] is None
                   or now - rec["last_probe_at"] >= PROBE_INTERVAL)
            if due:
                rec["last_probe_at"] = now
                rec["probe_streak"] += 1
                rec["probes"].append({
                    "time": now, "signal": signal, "ok": True,
                    "streak": rec["probe_streak"],
                })
                _emit("probe", site, {
                    "ok": True, "streak": rec["probe_streak"],
                    "required": PROBE_SUCCESS_REQUIRED,
                })
                if rec["probe_streak"] >= PROBE_SUCCESS_REQUIRED:
                    _transition(rec, site, CLOSED, reason="recovered")
            else:
                _emit("probe_skipped", site, {"reason": "interval"})
        return (not white) and rec["state"] != CLOSED

    weight = SIGNAL_WEIGHTS.get(signal)
    if weight is None:
        return (not white) and rec["state"] != CLOSED

    rec["votes"].append((now, weight, signal))
    _prune(rec)
    score = sum(vote[1] for vote in rec["votes"])
    _emit("vote", site, {
        "signal": signal, "weight": weight, "tally": score,
        "threshold": TRIP_THRESHOLD,
    })

    if white:
        return False

    if rec["state"] == HALF_OPEN:
        rec["probe_streak"] = 0
        rec["last_probe_at"] = now
        rec["probes"].append({
            "time": now, "signal": signal, "ok": False, "streak": 0,
        })
        _emit("probe", site, {"ok": False, "signal": signal, "streak": 0})
        _transition(rec, site, OPEN, reason="probe_failed")
    elif rec["state"] == CLOSED and score >= TRIP_THRESHOLD:
        _transition(rec, site, OPEN, reason="threshold")

    return rec["state"] != CLOSED


def allow(site):
    """是否放行当前请求；open 冷却后按探针间隔低频放行。"""
    if is_whitelisted(site):
        return True
    rec = _record(site)
    _maintain(rec, site)
    if rec["state"] == CLOSED:
        return True
    if rec["state"] == OPEN:
        return False
    if rec["last_probe_at"] is None:
        return True
    return _now() - rec["last_probe_at"] >= PROBE_INTERVAL
