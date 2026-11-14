"""多信号加权投票的站点熔断决策模块（纯标准库）。

五类封禁信号按站点累计权重，加权票数超过熔断阈值才熔断；
熔断后进入观察态，低频探针连续成功才恢复；支持带过期时间的
人工白名单。票数、状态迁移和探针结果通过 logging 与 events() 输出。
"""

import logging
import threading

logger = logging.getLogger("blockvote")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

# 五类封禁信号及其权重：403 为强信号，权重高于阈值，可单独熔断；
# 其余为弱信号，需要多个信号或多次出现累计过阈。
SIGNAL_WEIGHTS = {
    "403": 4,
    "captcha": 2,
    "empty": 2,
    "redirect": 1,
    "ratelimit": 2,
}
SUCCESS_SIGNAL = "200"

# 加权票数严格大于该阈值才熔断（403 权重 4，单次即过阈）。
BLOCK_THRESHOLD = 3

# 熔断后冷却时长，冷却结束才允许第一次探针。
COOLDOWN_SECONDS = 60.0
# 观察态下两次探针之间的最小间隔（低频探针）。
PROBE_INTERVAL_SECONDS = 30.0
# 观察态连续探针成功次数达到该值即解除熔断。
RECOVERY_SUCCESSES = 3
# 投票窗口：窗口内的信号才参与累计，过期信号自动剔除。
VOTE_WINDOW_SECONDS = 300.0

CLOSED = "closed"          # 正常放行
OPEN = "open"              # 熔断停用
HALF_OPEN = "half_open"    # 观察态，低频探针验证

_BLOCKED_STATES = (OPEN, HALF_OPEN)


class _BlockedSet(set):
    """与旧接口兼容的 _blocked 集合，clear 时重置全部模块状态。"""

    def clear(self):
        _reset()


_lock = threading.RLock()
_blocked = _BlockedSet()
_state = {}
_whitelist = {}
_events = []
_now = None  # 测试可替换的时钟，默认使用 monotonic


def _clock():
    import time

    return _now() if _now is not None else time.monotonic()


def _new_site():
    return {
        "state": CLOSED,
        "votes": [],
        "probe_successes": 0,
        "next_probe_at": 0.0,
    }


def _record(kind, site, **detail):
    event = {"kind": kind, "site": site, "time": _clock()}
    event.update(detail)
    _events.append(event)
    logger.info("%s site=%s %s", kind, site, detail)
    return event


def _whitelisted(site, now):
    """返回当前是否在白名单内，并惰性清理过期条目。"""
    expires_at = _whitelist.get(site)
    if expires_at is None:
        return False
    if expires_at <= now:
        del _whitelist[site]
        _record("whitelist_expired", site, expires_at=expires_at)
        return False
    return True


def add_whitelist(site, ttl_seconds):
    """人工加白：ttl_seconds 后自动过期，过期后恢复底层熔断判定。"""
    if ttl_seconds is None or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive number")
    with _lock:
        now = _clock()
        expires_at = now + ttl_seconds
        _whitelist[site] = expires_at
        _record("whitelist_added", site, ttl_seconds=ttl_seconds,
                expires_at=expires_at)
        return expires_at


def _transition(site, data, new_state, **detail):
    old_state = data["state"]
    if old_state == new_state:
        return
    data["state"] = new_state
    if new_state in _BLOCKED_STATES:
        _blocked.add(site)
    else:
        _blocked.discard(site)
    _record("state_transition", site, frm=old_state, to=new_state, **detail)


def observe(site, signal):
    """上报一个信号，返回该站点当前是否处于停用（含观察态）。

    封禁信号按权重计入站点投票窗口，过阈则熔断；成功信号（200）
    在正常态重置投票，在观察态作为探针参与恢复判定。
    """
    with _lock:
        now = _clock()
        if _whitelisted(site, now):
            _record("whitelist_hit", site, signal=signal)
            return False

        data = _state.get(site)
        if data is None:
            data = _new_site()
            _state[site] = data

        if signal == SUCCESS_SIGNAL:
            _handle_success(site, data, now)
        else:
            _handle_ban(site, data, signal, now)

        return data["state"] in _BLOCKED_STATES


def _prune(site, data, now):
    fresh = [
        (ts, sig)
        for ts, sig in data["votes"]
        if now - ts <= VOTE_WINDOW_SECONDS
    ]
    if len(fresh) != len(data["votes"]):
        data["votes"] = fresh


def _tally(data):
    breakdown = {}
    total = 0
    for _ts, sig in data["votes"]:
        weight = SIGNAL_WEIGHTS.get(sig, 0)
        breakdown[sig] = breakdown.get(sig, 0) + weight
        total += weight
    return total, breakdown


def _handle_ban(site, data, signal, now):
    weight = SIGNAL_WEIGHTS.get(signal)
    if weight is None:
        _record("unknown_signal", site, signal=signal)
        return

    if data["state"] == HALF_OPEN:
        # 观察态探针期间再次命中封禁信号：恢复失败，退回熔断并重新冷却。
        data["probe_successes"] = 0
        data["next_probe_at"] = now + COOLDOWN_SECONDS
        _record("probe_failed", site, signal=signal, weight=weight)
        _transition(site, data, OPEN, reason=signal)
        return

    _prune(site, data, now)
    data["votes"].append((now, signal))
    total, breakdown = _tally(data)
    _record("vote", site, signal=signal, weight=weight, total=total,
            threshold=BLOCK_THRESHOLD, breakdown=dict(breakdown))

    if total > BLOCK_THRESHOLD:
        data["probe_successes"] = 0
        data["next_probe_at"] = now + COOLDOWN_SECONDS
        _transition(site, data, OPEN, total=total, votes=len(data["votes"]))


def _handle_success(site, data, now):
    state = data["state"]
    if state == CLOSED:
        if data["votes"]:
            data["votes"] = []
            _record("votes_reset", site, signal=SUCCESS_SIGNAL)
        return

    if state == OPEN:
        _maybe_enter_half_open(site, data, now)
        if data["state"] != HALF_OPEN:
            _record("probe_skipped", site, reason="cooldown",
                    next_probe_at=data["next_probe_at"])
            return

    # HALF_OPEN：低频探针，未到探针间隔则跳过。
    if now < data["next_probe_at"]:
        _record("probe_skipped", site, reason="interval",
                next_probe_at=data["next_probe_at"])
        return

    data["next_probe_at"] = now + PROBE_INTERVAL_SECONDS
    data["probe_successes"] += 1
    _record("probe_ok", site,
            consecutive=data["probe_successes"],
            required=RECOVERY_SUCCESSES)
    if data["probe_successes"] >= RECOVERY_SUCCESSES:
        data["votes"] = []
        data["probe_successes"] = 0
        data["next_probe_at"] = 0.0
        _transition(site, data, CLOSED, reason="recovered")


def _maybe_enter_half_open(site, data, now):
    if data["state"] == OPEN and now >= data["next_probe_at"]:
        # 下一次上报立即作为首个探针，间隔在探针完成后再排。
        data["next_probe_at"] = now
        _transition(site, data, HALF_OPEN)


def allow(site):
    """站点当前是否放行；白名单在有效期内始终放行。"""
    with _lock:
        now = _clock()
        if _whitelisted(site, now):
            return True
        data = _state.get(site)
        return data is None or data["state"] not in _BLOCKED_STATES


def votes(site):
    """返回 (窗口内加权总票数, 各信号加权票数明细)。"""
    with _lock:
        data = _state.get(site)
        if data is None:
            return 0, {}
        _prune(site, data, _clock())
        return _tally(data)


def status(site):
    """返回站点当前状态：closed / open / half_open。"""
    with _lock:
        now = _clock()
        if _whitelisted(site, now):
            return CLOSED
        data = _state.get(site)
        return CLOSED if data is None else data["state"]


def events(site=None):
    """返回投票、状态迁移、探针与白名单事件列表（可按站点过滤）。"""
    with _lock:
        if site is None:
            return list(_events)
        return [event for event in _events if event["site"] == site]


def stats(site):
    """返回站点完整决策快照，便于观测票数与探针进度。"""
    with _lock:
        now = _clock()
        data = _state.get(site)
        total, breakdown = votes(site) if data is not None else (0, {})
        return {
            "site": site,
            "state": status(site),
            "whitelisted": _whitelisted(site, now),
            "votes_total": total,
            "votes_breakdown": breakdown,
            "threshold": BLOCK_THRESHOLD,
            "probe_successes": 0 if data is None else data["probe_successes"],
            "recovery_required": RECOVERY_SUCCESSES,
            "next_probe_at": 0.0 if data is None else data["next_probe_at"],
        }


def _reset():
    _state.clear()
    _whitelist.clear()
    _events.clear()
