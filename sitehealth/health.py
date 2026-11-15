"""站点健康评分：分项计分、近期加权、快降慢升、观察名单。"""

from __future__ import annotations

import time

PART_WEIGHTS = {"success": 0.4, "latency": 0.2, "throttle": 0.2, "content": 0.2}
PART_NAMES = {"success": "成功率", "latency": "延迟", "throttle": "限流", "content": "内容"}

HALF_LIFE = 1800.0       # 近期数据半衰期（秒），越久的事件权重越低
FAST_ALPHA = 0.6         # 恶化步长（快降）
SLOW_ALPHA = 0.15        # 恢复步长（慢升）
STREAK_SNAP = 3          # 连续失败达到该值，分项直接对齐目标分
WATCH_THRESHOLD = 0.5    # 总分低于该值进入观察名单
RECOVER_THRESHOLD = 0.7  # 总分高于该值记一个恢复日
RECOVER_DAYS = 3         # 连续恢复天数达标后移出观察名单
TREND_WINDOW = 3600.0    # 趋势对比窗口（秒）
RTT_GOOD = 0.5           # 延迟满分阈值（秒）
RTT_BAD = 5.0            # 延迟零分阈值（秒）


class _SiteState:
    def __init__(self):
        self.events = []    # [(ts, event)]
        self.parts = {k: 1.0 for k in PART_WEIGHTS}
        self.score = 1.0
        self.streak = 0     # 连续失败数
        self.history = []   # 每次分数变化记录（含原因）
        self.watch = None   # {"since", "good_days", "last_day"}
        self.watch_log = []  # 进出观察名单记录


_sites = {}


def reset():
    """清空全部站点状态（测试用）。"""
    _sites.clear()


def _event_view(event):
    ok = bool(event.get("ok", True))
    throttled = event.get("status") == 429 or event.get("throttled") is True
    anomaly = event.get("content_ok") is False or event.get("anomaly") is True
    return ok, throttled, anomaly, event.get("rtt")


def _latency_score(rtt, ok):
    if not ok:
        return 0.0  # 失败请求等于没拿到可用响应
    if rtt is None:
        return None  # 无延迟数据，不参与延迟分项
    if rtt <= RTT_GOOD:
        return 1.0
    if rtt >= RTT_BAD:
        return 0.0
    return 1.0 - (rtt - RTT_GOOD) / (RTT_BAD - RTT_GOOD)


def _targets(st, now):
    """按半衰期加权计算各分项目标分，近期事件权重更高。"""
    acc = {k: [0.0, 0.0] for k in PART_WEIGHTS}  # [加权和, 权重和]
    for ts, ev in st.events:
        w = 0.5 ** (max(0.0, now - ts) / HALF_LIFE)
        ok, throttled, anomaly, rtt = _event_view(ev)
        acc["success"][0] += w * (1.0 if ok else 0.0)
        acc["success"][1] += w
        lat = _latency_score(rtt, ok)
        if lat is not None:
            acc["latency"][0] += w * lat
            acc["latency"][1] += w
        acc["throttle"][0] += w * (0.0 if throttled else 1.0)
        acc["throttle"][1] += w
        acc["content"][0] += w * (0.0 if anomaly else 1.0)
        acc["content"][1] += w
    return {k: (s / w if w > 0 else 1.0) for k, (s, w) in acc.items()}


def _reason(st, old_parts, ok, throttled, anomaly, rtt):
    causes = []
    if not ok:
        causes.append(f"第{st.streak}次连续失败" if st.streak > 1 else "请求失败")
    if throttled:
        causes.append("限流(429)")
    if anomaly:
        causes.append("内容异常")
    if ok and rtt is not None and rtt > RTT_GOOD:
        causes.append(f"响应缓慢{rtt:.2f}s")
    moves = {k: st.parts[k] - old_parts[k] for k in PART_WEIGHTS}
    part = max(moves, key=lambda k: abs(moves[k]))
    move = moves[part]
    if abs(move) < 1e-9:
        return "分项无显著变化"
    direction = "下降" if move < 0 else "回升"
    text = f"{PART_NAMES[part]}{direction} {old_parts[part]:.2f}→{st.parts[part]:.2f}"
    if causes:
        text += "（" + "、".join(causes) + "）"
    elif move > 0:
        text += "（恢复中缓慢回升）"
    return text


def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _update_watch(st, ts):
    if st.watch is None:
        if st.score < WATCH_THRESHOLD:
            st.watch = {"since": ts, "good_days": 0, "last_day": None}
            st.watch_log.append({
                "ts": ts, "action": "enter", "score": round(st.score, 4),
                "reason": f"总分 {st.score:.2f} 低于阈值 {WATCH_THRESHOLD}，暂停正常派单",
            })
        return
    if st.score >= RECOVER_THRESHOLD:
        day = _day(ts)
        if day != st.watch["last_day"]:
            st.watch["good_days"] += 1
            st.watch["last_day"] = day
        if st.watch["good_days"] >= RECOVER_DAYS:
            st.watch_log.append({
                "ts": ts, "action": "exit", "score": round(st.score, 4),
                "reason": f"连续 {RECOVER_DAYS} 天总分≥{RECOVER_THRESHOLD}，恢复正常派单",
            })
            st.watch = None
    else:
        st.watch["good_days"] = 0
        st.watch["last_day"] = None


def _ingest(st, site, event, ts):
    st.events.append((ts, dict(event)))
    ok, throttled, anomaly, rtt = _event_view(event)
    st.streak = 0 if ok else st.streak + 1
    targets = _targets(st, ts)
    snap = st.streak >= STREAK_SNAP
    old_parts = dict(st.parts)
    for k, target in targets.items():
        cur = st.parts[k]
        if target < cur:
            alpha = 1.0 if snap else FAST_ALPHA  # 快降
        else:
            alpha = SLOW_ALPHA                    # 慢升
        st.parts[k] = cur + alpha * (target - cur)
    old_score = st.score
    st.score = sum(st.parts[k] * PART_WEIGHTS[k] for k in PART_WEIGHTS)
    st.history.append({
        "ts": ts,
        "score": round(st.score, 4),
        "delta": round(st.score - old_score, 4),
        "parts": {k: round(v, 4) for k, v in st.parts.items()},
        "reason": _reason(st, old_parts, ok, throttled, anomaly, rtt),
    })
    _update_watch(st, ts)


def _trend(st):
    if not st.history:
        return "flat"
    now = st.history[-1]["ts"]
    base = st.history[0]["score"]
    for rec in st.history:
        if rec["ts"] <= now - TREND_WINDOW:
            base = rec["score"]
        else:
            break
    delta = st.score - base
    if delta > 0.02:
        return "up"
    if delta < -0.02:
        return "down"
    return "flat"


def _snapshot(st, site):
    return {
        "site": site,
        "score": round(st.score, 4),
        "parts": {k: round(v, 4) for k, v in st.parts.items()},
        "trend": _trend(st),
        "dispatch": "watch" if st.watch else "normal",
        "events": len(st.events),
        "last_change": st.history[-1] if st.history else None,
    }


def report(site, event, ts=None):
    """上报一个站点事件，返回最新评分快照。"""
    st = _sites.setdefault(site, _SiteState())
    _ingest(st, site, event, time.time() if ts is None else ts)
    return _snapshot(st, site)


def score(site):
    """当前评分：各分项、总分、趋势、最近一次变化原因。"""
    return _snapshot(_sites.setdefault(site, _SiteState()), site)


def history(site, day=None):
    """评分变化历史，可按天（YYYY-MM-DD）过滤回看。"""
    st = _sites.get(site)
    records = [] if st is None else st.history
    if day is not None:
        records = [r for r in records if _day(r["ts"]) == day]
    return [dict(r) for r in records]


def daily(site):
    """按天汇总评分变化，供值班复核。"""
    st = _sites.get(site)
    out = {}
    for rec in ([] if st is None else st.history):
        d = out.setdefault(_day(rec["ts"]), {
            "day": _day(rec["ts"]), "min": 1.0, "max": 0.0,
            "last": None, "changes": 0, "reasons": [],
        })
        d["min"] = min(d["min"], rec["score"])
        d["max"] = max(d["max"], rec["score"])
        d["last"] = rec["score"]
        d["changes"] += 1
        d["reasons"].append(rec["reason"])
    return [out[k] for k in sorted(out)]


def watchlist():
    """当前观察名单。"""
    return [
        {"site": s, "watch": dict(st.watch), "score": round(st.score, 4)}
        for s, st in sorted(_sites.items()) if st.watch is not None
    ]


def replay(site):
    """从原始事件重新计算评分历史（回放），用于复核与审计。"""
    st = _sites.get(site)
    if st is None:
        return {"site": site, "history": [], "final": score(site)}
    fresh = _SiteState()
    for ts, ev in st.events:
        _ingest(fresh, site, ev, ts)
    return {"site": site, "history": list(fresh.history), "final": _snapshot(fresh, site)}


def export(site):
    """按站点导出全部评分数据（可 JSON 序列化）。"""
    st = _sites.setdefault(site, _SiteState())
    return {
        "site": site,
        "snapshot": _snapshot(st, site),
        "events": [dict(ev, ts=ts) for ts, ev in st.events],
        "history": list(st.history),
        "daily": daily(site),
        "watch": None if st.watch is None else dict(st.watch),
        "watch_log": list(st.watch_log),
    }


def export_watchlist():
    """导出观察名单，供值班同学定期复核。"""
    return {"sites": [export(w["site"]) for w in watchlist()]}
