"""按站点自适应的采集限速。

每个站点一份状态，互不影响：

- 连续成功达到阈值后缓慢提速（+10%/次，有上限、有提速间隔）；
- 返回 429 立即折半降速并进入指数退避冷却，冷却期间不放行；
- 速率和并发共用一个预算：并发上限由 ``BUDGET / rate`` 推导，降速时冻结在
  降速前的水平，不会一边限速一边放大并发；突发请求受令牌桶约束，无法绕开限速；
- 冷却结束后保持降速后的低速，靠连续成功逐步恢复，需要双倍成功连击；
- 提速 / 降速 / 冷却结束事件按站点保留 24 小时，可回看最近一小时、可按站点导出；
- 额度不足时返回排队提示，事件同样留档，可回看。
"""

import csv
import io
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field

# 速率（请求/秒）与调整参数
START_RATE = 1.0
MIN_RATE = 0.1
MAX_RATE = 8.0
SUCCESS_THRESHOLD = 5          # 连续成功多少次后允许提速
SPEEDUP_PCT = 0.10             # 每次提速比例
SPEEDUP_MIN = 0.02             # 每次提速的绝对下限（速率为最低档时也能推进）
SPEEDUP_MAX = 1.0              # 每次提速的绝对上限，防止跳变
SLOWDOWN_FACTOR = 0.5          # 429 后保留的速率比例
BACKOFF_BASE = 1.0             # 首次冷却秒数，之后指数翻倍
MAX_COOLDOWN = 30.0            # 单次冷却上限
BACKOFF_RESET = 300.0          # 距上次 429 超过这么久，退避从头算（新的一次事故）
ADJUST_INTERVAL = 1.0          # 两次提速之间的最短间隔

# 速率与并发共享的预算：并发上限 = BUDGET / rate
BUDGET = 8.0
MAX_CONCURRENCY = 16
BUCKET_CAPACITY = 1.0          # 令牌桶容量，同一时刻最多 1 个突发令牌

# 留档参数
RETENTION_SECONDS = 24 * 3600.0   # 调整过程按站点保留一天
RECENT_HOURS = 1.0                # 默认回看最近一小时
MAX_ADJUSTMENTS = 5000            # 条数兜底，极端高频下调时先淘汰最旧的
MAX_DENIALS = 5000
DENIAL_LOG_INTERVAL = 10.0        # 同一原因最多每 10 秒记一条，避免重试刷屏

ADJUST_KINDS = ("speedup", "slowdown", "recovered")

_lock = threading.RLock()
_sites = {}
_now = time.monotonic
_sleep = time.sleep
_wall_offset = None


@dataclass
class _Site:
    rate: float = START_RATE
    tokens: float = BUCKET_CAPACITY
    last_refill: float = 0.0
    inflight: int = 0
    limit: int = 8
    limit_cap: int = MAX_CONCURRENCY
    cooldown_until: float = 0.0
    cooldown_seconds: float = 0.0
    consecutive_429: int = 0
    last_429_at: float = float("-inf")
    consecutive_ok: int = 0
    pre_429_rate: float = 0.0
    recovering: bool = False
    last_up: float = float("-inf")
    adjustments: deque = field(default_factory=lambda: deque(maxlen=MAX_ADJUSTMENTS))
    denials: deque = field(default_factory=lambda: deque(maxlen=MAX_DENIALS))
    last_denial: dict = field(default_factory=dict)


def _budget_limit(rate):
    """速率与并发共享一个预算：rate 越高，并发上限越低。"""
    return max(1, min(MAX_CONCURRENCY, int(BUDGET / max(rate, 1e-9))))


def _sync_limit(state):
    """并发上限 = min(预算推导值, 冻结上限)。"""
    state.limit = max(1, min(_budget_limit(state.rate), state.limit_cap, MAX_CONCURRENCY))


def _get(site):
    state = _sites.get(site)
    if state is None:
        now = _now()
        state = _Site(last_refill=now, limit=_budget_limit(START_RATE))
        _sites[site] = state
    return state


def _clamp(rate):
    return max(MIN_RATE, min(MAX_RATE, rate))


def _finish_recovery(state):
    """恢复完成：解除并发冻结，并发额度回到与当前速率同一预算。

    完全恢复到限流前的水平才把退避次数清零，同一次事故内继续指数退避。
    """
    state.recovering = False
    state.consecutive_429 = 0
    state.limit_cap = MAX_CONCURRENCY
    _sync_limit(state)


def _apply_rate(state, new_rate):
    """应用新速率并同步并发上限。

    降速会把并发上限冻结在当时的水平，预算推导出的额度变大也不会被用上，
    避免一边限速一边放大并发；恢复完成后才解除冻结。
    """
    new = _clamp(new_rate)
    if abs(new - state.rate) < 1e-9:
        return False
    if new < state.rate:
        state.limit_cap = min(state.limit_cap, state.limit)
    state.rate = new
    _sync_limit(state)
    return True


def _wall_iso(ts):
    global _wall_offset
    if ts is None:
        return None
    if _wall_offset is None:
        _wall_offset = time.time() - _now()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts + _wall_offset))


def _base_record(state, now, kind, reason, prev_rate, status=None, rtt=None):
    return {
        "ts": round(now, 3),
        "at": _wall_iso(now),
        "kind": kind,
        "reason": reason,
        "prev_rate": round(prev_rate, 3),
        "rate": round(state.rate, 3),
        "status": status,
        "rtt": round(rtt, 3) if isinstance(rtt, (int, float)) else rtt,
        "cooldown_in": round(max(0.0, state.cooldown_until - now), 3),
        "concurrency_limit": state.limit,
    }


def _record_adjustment(state, now, kind, reason, prev_rate, status=None, rtt=None):
    state.adjustments.append(_base_record(state, now, kind, reason, prev_rate, status, rtt))


def _record_denial(state, now, reason, wait_s):
    last = state.last_denial.get(reason)
    if last is not None and now - last < DENIAL_LOG_INTERVAL:
        return
    state.last_denial[reason] = now
    entry = _base_record(state, now, "denied", reason, state.rate)
    entry["wait"] = round(max(0.0, wait_s), 3)
    state.denials.append(entry)


def _trim(state, now):
    adjust_cutoff = now - RETENTION_SECONDS
    while state.adjustments and state.adjustments[0]["ts"] < adjust_cutoff:
        state.adjustments.popleft()
    while state.denials and state.denials[0]["ts"] < adjust_cutoff:
        state.denials.popleft()


def _refresh(state, now):
    """推进令牌桶、结束冷却、清理过期记录。"""
    if now > state.last_refill:
        state.tokens = min(BUCKET_CAPACITY, state.tokens + (now - state.last_refill) * state.rate)
        state.last_refill = now
    if state.cooldown_until and now >= state.cooldown_until:
        ended_at = state.cooldown_until
        state.cooldown_until = 0.0
        state.cooldown_seconds = 0.0
        # 冷却结束不回到 429 前的速度：保持降速后的低速，交给连续成功慢慢恢复。
        # consecutive_429 不清零，反复被限流时退避时间继续翻倍，不会每次重来。
        _record_adjustment(state, ended_at, "recovered", "cooldown_end", state.rate)
    _trim(state, now)


def _cooldown_in(state, now):
    if not state.cooldown_until:
        return 0.0
    return max(0.0, state.cooldown_until - now)


def _last_adjustment(state):
    if not state.adjustments:
        return None
    last = state.adjustments[-1]
    return {
        "kind": last["kind"],
        "reason": last["reason"],
        "at": last["at"],
        "prev_rate": last["prev_rate"],
        "rate": last["rate"],
    }


def _view(state, site, now):
    """统一额度视图：remaining 表示此刻还能放行几个请求（冷却期为 0）。"""
    cooldown = _cooldown_in(state, now)
    slots = max(0, state.limit - state.inflight)
    tokens = max(0.0, state.tokens)
    if cooldown > 0 or tokens < 1.0:
        remaining = 0
    else:
        remaining = slots
    return {
        "site": site,
        "rate": round(state.rate, 3),
        "remaining": remaining,
        "remaining_concurrency": slots,
        "remaining_tokens": round(tokens, 3),
        "concurrency_limit": state.limit,
        "inflight": state.inflight,
        "cooldown_until": _wall_iso(state.cooldown_until or None),
        "cooldown_in": round(cooldown, 3),
        "last_adjust": _last_adjustment(state),
    }


def _reason_hint(reason):
    return {
        "ok": "",
        "cooldown": "site is cooling down after 429; retry after cooldown_in seconds",
        "concurrency_full": "all slots in the shared budget are busy",
        "quota_exhausted": "pacing quota exhausted; queue until the next token",
    }.get(reason, "")


def acquire(site, wait=False, max_wait=5.0):
    """申请一个采集额度。

    默认不阻塞：额度不足时返回 ``ok=False``，并给出 ``wait`` / ``hint``。
    ``wait=True`` 时最多阻塞 ``max_wait`` 秒排队等待。
    """
    start = _now()
    while True:
        with _lock:
            state = _get(site)
            now = _now()
            _refresh(state, now)

            cooldown = _cooldown_in(state, now)
            if cooldown > 0:
                reason, wait_s = "cooldown", cooldown
            elif state.inflight >= state.limit:
                reason, wait_s = "concurrency_full", 0.1
            elif state.tokens < 1.0:
                reason, wait_s = "quota_exhausted", (1.0 - state.tokens) / state.rate
            else:
                state.tokens -= 1.0
                state.inflight += 1
                result = _view(state, site, now)
                result.update({"ok": True, "reason": "ok", "wait": 0.0, "hint": ""})
                return result

            result = _view(state, site, now)
            result.update(
                {"ok": False, "reason": reason, "wait": round(wait_s, 3), "hint": _reason_hint(reason)}
            )

            if not wait:
                _record_denial(state, now, reason, wait_s)
                return result

        elapsed = _now() - start
        if elapsed + result["wait"] > max_wait:
            with _lock:
                _record_denial(_get(site), _now(), result["reason"], result["wait"])
            result["hint"] = "queue wait exceeds max_wait; " + result["hint"]
            return result
        before = _now()
        _sleep(max(0.001, result["wait"]))
        if _now() - before < 1e-9:
            # 时钟不前进（例如注入的假时钟），直接返回提示，避免空转
            result["hint"] = "queue wait cannot progress; " + result["hint"]
            return result


def feedback(site, status, rtt=None):
    """回填一次请求结果，按 429 / 成功调整速率并留下记录。"""
    with _lock:
        state = _get(site)
        now = _now()
        _refresh(state, now)
        if state.inflight > 0:
            state.inflight -= 1

        try:
            code = int(status)
        except (TypeError, ValueError):
            code = 0

        prev = state.rate
        adjusted = False
        kind = "hold"
        reason = "no_change"
        threshold = SUCCESS_THRESHOLD

        if code == 429 and _cooldown_in(state, now) > 0:
            # 冷却期内回来的 429 多是限流前发出的请求，不再重复折半；
            # 否则一批慢响应的 429 会把速率两三步打到地板，来回跳。
            reason = "cooldown_active"
        elif code == 429:
            # 记住被限流前的速度：恢复阶段最多爬回这个水平，
            # 反复限流时目标随实际失败点一起下移
            state.pre_429_rate = prev if not state.recovering else min(state.pre_429_rate, prev)
            state.recovering = True
            state.consecutive_ok = 0
            if now - state.last_429_at > BACKOFF_RESET:
                state.consecutive_429 = 0
            state.last_429_at = now
            state.consecutive_429 += 1
            cooldown = min(MAX_COOLDOWN, BACKOFF_BASE * (2 ** (state.consecutive_429 - 1)))
            state.cooldown_seconds = cooldown
            state.cooldown_until = max(state.cooldown_until, now + cooldown)
            adjusted = _apply_rate(state, prev * SLOWDOWN_FACTOR)
            # 已在最低档时速率不再下降，但冷却照旧生效，照样留档说明原因
            kind = "slowdown"
            reason = "status_429" if adjusted else "rate_floor"
        elif 200 <= code < 300:
            if _cooldown_in(state, now) > 0:
                # 冷却期内回填的多是 429 之前发出的请求，不计入恢复连击
                reason = "cooldown_active"
            else:
                state.consecutive_ok += 1
                if state.recovering:
                    # 恢复期需要双倍成功次数，且始终从低速起步
                    threshold = SUCCESS_THRESHOLD * 2
                if state.rate >= MAX_RATE - 1e-9:
                    reason = "max_rate"
                elif state.consecutive_ok < threshold:
                    reason = "recovering" if state.recovering else "success_streak_building"
                elif now - state.last_up < ADJUST_INTERVAL:
                    reason = "adjust_cooldown"
                else:
                    step = min(SPEEDUP_MAX, max(SPEEDUP_MIN, state.rate * SPEEDUP_PCT))
                    adjusted = _apply_rate(state, state.rate + step)
                    if adjusted:
                        state.last_up = now
                        state.consecutive_ok = 0
                        kind, reason = "speedup", "success_streak"
                        if state.recovering and state.rate >= state.pre_429_rate - 1e-9:
                            # 回到 429 前的速度，恢复结束，解除并发冻结
                            _finish_recovery(state)
                if state.recovering and state.rate >= MAX_RATE - 1e-9:
                    _finish_recovery(state)
        else:
            state.consecutive_ok = 0
            reason = "error_hold"

        if kind in ADJUST_KINDS:
            _record_adjustment(state, now, kind, reason, prev, code, rtt)

        if kind == "slowdown":
            hint = "rate %.2f -> %.2f, cooldown %.1fs" % (prev, state.rate, state.cooldown_seconds)
        elif kind == "speedup":
            hint = "rate %.2f -> %.2f" % (prev, state.rate)
        else:
            hint = "rate unchanged (%s)" % reason

        result = _view(state, site, now)
        result.update(
            {
                "status": code,
                "rtt": round(rtt, 3) if isinstance(rtt, (int, float)) else rtt,
                "prev_rate": round(prev, 3),
                "adjusted": adjusted,
                "reason": reason,
                "hint": hint,
            }
        )
        return result


def release(site):
    """归还一个并发名额（请求失败、没有结果可回填时使用）。"""
    with _lock:
        state = _get(site)
        if state.inflight > 0:
            state.inflight -= 1
        return _view(state, site, _now())


def _matches(entry, kinds):
    if kinds is None:
        return True
    if isinstance(kinds, str):
        kinds = (kinds,)
    return entry["kind"] in kinds


def records(site, hours=RECENT_HOURS, kind=None):
    """按站点回看最近的调整过程，默认最近一小时的提速 / 降速 / 恢复事件。

    ``kind`` 可传单个类型或类型集合；``"denied"`` 取额度不足记录，
    ``"all"`` 取调整与额度不足的合集，``None`` 取调整记录。
    """
    with _lock:
        state = _get(site)
        now = _now()
        _refresh(state, now)
        cutoff = now - hours * 3600.0
        if kind == "denied":
            source = list(state.denials)
        elif kind == "all":
            source = sorted(
                list(state.adjustments) + list(state.denials), key=lambda e: e["ts"]
            )
        else:
            source = list(state.adjustments)
        kinds = ADJUST_KINDS if kind is None else None if kind == "all" else kind
        return [dict(e) for e in source if e["ts"] >= cutoff and _matches(e, kinds)]


def export_records(site, hours=24.0, fmt="json", kind=None):
    """按站点导出留档记录，默认导出保留的全部一天，支持 json / csv。"""
    entries = records(site, hours=hours, kind=kind)
    fmt = (fmt or "json").lower()
    if fmt == "json":
        return json.dumps(
            {
                "site": site,
                "hours": hours,
                "retention_hours": RETENTION_SECONDS / 3600.0,
                "count": len(entries),
                "records": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
    if fmt == "csv":
        columns = [
            "at", "kind", "reason", "prev_rate", "rate", "status", "rtt",
            "cooldown_in", "concurrency_limit", "wait",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry)
        return buffer.getvalue()
    raise ValueError("unsupported export format: %r" % (fmt,))


def status(site):
    """值班视图：当前速度、剩余额度、冷却、最近调整与额度不足情况。"""
    with _lock:
        state = _get(site)
        now = _now()
        _refresh(state, now)
        recent = now - RECENT_HOURS * 3600.0
        result = _view(state, site, now)
        cooldown = _cooldown_in(state, now)
        if cooldown > 0:
            reason = "cooldown"
        elif state.inflight >= state.limit:
            reason = "concurrency_full"
        elif state.tokens < 1.0:
            reason = "quota_exhausted"
        else:
            reason = "ok"
        result.update(
            {
                "ok": reason == "ok",
                "reason": reason,
                "hint": _reason_hint(reason),
                "min_rate": MIN_RATE,
                "max_rate": MAX_RATE,
                "recovering": state.recovering,
                "success_streak": state.consecutive_ok,
                "records_1h": sum(1 for e in state.adjustments if e["ts"] >= recent),
                "records_total": len(state.adjustments),
                "denied_recent": any(e["ts"] >= recent for e in state.denials),
                "denied_total": len(state.denials),
                "retention_hours": RETENTION_SECONDS / 3600.0,
            }
        )
        return result


def set_clock(clock=None, sleeper=None):
    """注入时钟和休眠函数（测试 / 演示用）；传 None 恢复系统实现。"""
    global _now, _sleep, _wall_offset
    _now = time.monotonic if clock is None else clock
    _sleep = time.sleep if sleeper is None else sleeper
    _wall_offset = None


def reset():
    """清空所有站点状态。"""
    with _lock:
        _sites.clear()
