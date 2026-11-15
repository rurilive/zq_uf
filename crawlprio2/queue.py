"""采集队列：站点/租户两级配额 + 等待老化提权 + 有配额上限的高优插队。

默认配置退化为纯 FIFO（兼容既有行为）；用 configure() 开启各项机制。
仅依赖 Python 标准库。

口径：
- 有效优先级 = priority + min(等待秒数 * aging_rate, aging_cap)，aging_cap=0 关闭提权。
- site_quota / tenant_quota：同一站点/租户连续出队上限，触限后跳过队首，
  取第一个未触限任务（reason=quota_skip）；全队同源时放行，避免饿死。
- 插队：有效优先级 >= preempt_threshold 且严格高于队首才可插队，
  每站点每 preempt_window 秒限 preempt_quota 次，插队消耗配额。
- 保底：每 guarantee_window 次出队，最长等待的低优任务优先出队。
- 超时：等待超过 max_wait 秒按 timeout_action 降级（degrade）或丢弃（drop）。
"""

import time
from collections import deque

_DEFAULTS = {
    "site_quota": 0,            # 同一站点允许连续出队的最大次数，0 不限
    "tenant_quota": 0,          # 同一租户允许连续出队的最大次数，0 不限
    "aging_rate": 0.0,          # 每等待一秒获得的提权值
    "aging_cap": 0,             # 提权上限，0 表示关闭老化提权
    "preempt_threshold": None,  # 有效优先级达到该值才允许插队，None 关闭
    "preempt_quota": 0,         # 每站点每个窗口允许的插队次数
    "preempt_window": 60.0,     # 插队配额窗口长度（秒）
    "guarantee_window": 0,      # 每 N 次出队给低优任务保底一次，0 关闭
    "low_priority": 1,          # priority <= 该值视为低优
    "max_wait": 0.0,            # 等待超时秒数，0 不超时
    "timeout_action": "degrade",  # 超时处理：degrade 进降级队列 / drop 丢弃
    "clock": time.monotonic,    # 时钟，测试可注入
}

_config = dict(_DEFAULTS)


class _Record:
    __slots__ = ("task", "seq", "enqueued")

    def __init__(self, task, seq, enqueued):
        self.task = task
        self.seq = seq
        self.enqueued = enqueued


def _fresh_stats():
    return {
        "pushed": 0,
        "popped": 0,
        "preempted": 0,
        "aging_preempted": 0,
        "boosted": 0,
        "guaranteed": 0,
        "quota_skipped": 0,
        "degraded": 0,
        "dropped": 0,
        "by_site": {},
        "by_tenant": {},
        "total_wait": 0.0,
        "max_wait": 0.0,
    }


_queue = deque()       # 主队列
_degraded = deque()    # 降级队列（主队列排空后才出队）
_log = []              # 出队/丢弃记录
_seq = 0
_last_site = None
_last_tenant = None
_site_run = 0
_tenant_run = 0
_since_guarantee = 0
_preempt_used = {}     # site -> (window_start, count)
_stats = _fresh_stats()


def configure(**overrides):
    """更新配置，未知键报错。"""
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise KeyError("unknown config keys: %s" % sorted(unknown))
    action = overrides.get("timeout_action")
    if action is not None and action not in ("degrade", "drop"):
        raise ValueError("timeout_action must be 'degrade' or 'drop'")
    _config.update(overrides)


def reset():
    """清空队列、统计与配置（回到默认纯 FIFO）。"""
    global _seq, _last_site, _last_tenant, _site_run, _tenant_run
    global _since_guarantee, _stats
    _config.clear()
    _config.update(_DEFAULTS)
    _queue.clear()
    _degraded.clear()
    _log.clear()
    _preempt_used.clear()
    _seq = 0
    _last_site = _last_tenant = None
    _site_run = _tenant_run = 0
    _since_guarantee = 0
    _stats = _fresh_stats()


def push(task):
    """入队，返回当前队列长度与任务的优先级/站点/租户。"""
    global _seq
    rec = _Record(dict(task), _seq, _config["clock"]())
    _seq += 1
    _queue.append(rec)
    _stats["pushed"] += 1
    return {
        "queued": len(_queue),
        "priority": rec.task.get("priority", 0),
        "site": rec.task.get("site", ""),
        "tenant": rec.task.get("tenant", ""),
    }


def pop():
    """出队一个任务；返回任务字段外加 dequeue={reason, wait, boost}，空返回 None。"""
    now = _config["clock"]()
    _sweep_timeouts(now)
    if _queue:
        rec, reason = _select(now)
        _queue.remove(rec)
        return _commit(rec, reason, now)
    if _degraded:
        return _commit(_degraded.popleft(), "degraded", now)
    return None


def stats():
    """出队、配额、提权与等待的汇总快照。"""
    s = dict(_stats)
    s["by_site"] = dict(_stats["by_site"])
    s["by_tenant"] = dict(_stats["by_tenant"])
    s["queued"] = len(_queue)
    s["degraded_queued"] = len(_degraded)
    s["avg_wait"] = s["total_wait"] / s["popped"] if s["popped"] else 0.0
    s["preempt_used"] = {site: cnt for site, (_, cnt) in _preempt_used.items()}
    return s


def dequeue_log():
    """每次出队/丢弃的记录（含 reason、wait、boost）。"""
    return list(_log)


# ---- 内部 ----

def _site(rec):
    return rec.task.get("site", "")


def _tenant(rec):
    return rec.task.get("tenant", "")


def _priority(rec):
    return rec.task.get("priority", 0)


def _boost(rec, now):
    cap = _config["aging_cap"]
    if cap <= 0:
        return 0
    wait = max(0.0, now - rec.enqueued)
    return min(wait * _config["aging_rate"], cap)


def _effective(rec, now):
    return _priority(rec) + _boost(rec, now)


def _blocked(rec):
    """站点或租户的连续出队配额是否已触限。"""
    sq = _config["site_quota"]
    if sq and _site_run >= sq and _site(rec) == _last_site:
        return True
    tq = _config["tenant_quota"]
    if tq and _tenant_run >= tq and _tenant(rec) == _last_tenant:
        return True
    return False


def _select(now):
    head = _queue[0]
    # 1. 低优保底窗口
    gw = _config["guarantee_window"]
    if gw and _since_guarantee >= gw:
        lows = [r for r in _queue if _priority(r) <= _config["low_priority"]]
        if lows:
            return min(lows, key=lambda r: r.seq), "guarantee"
    # 2. 高优插队（消耗站点插队配额）
    cand = _preempt_candidate(now)
    if cand is not None:
        return cand
    # 3. 配额内 FIFO
    if _blocked(head):
        for rec in _queue:
            if not _blocked(rec):
                return rec, "quota_skip"
        # 全队同源，放行避免饿死
    return head, "fifo"


def _preempt_candidate(now):
    threshold = _config["preempt_threshold"]
    if threshold is None or _config["preempt_quota"] <= 0:
        return None
    head = _queue[0]
    head_eff = _effective(head, now)
    cands = [r for r in _queue if _effective(r, now) >= threshold]
    cands.sort(key=lambda r: (-_effective(r, now), r.seq))
    for rec in cands:
        if rec is head:
            return None  # 队首自己最优，无需插队
        if _effective(rec, now) <= head_eff:
            return None  # 同分或更低不插队，保持 FIFO
        if _preempt_available(_site(rec), now):
            if _priority(rec) >= threshold:
                return rec, "preempt"
            return rec, "aging_preempt"  # 靠老化提权达标
    return None


def _preempt_available(site, now):
    start, count = _preempt_used.get(site, (now, 0))
    if now - start >= _config["preempt_window"]:
        return True
    return count < _config["preempt_quota"]


def _consume_preempt(site, now):
    start, count = _preempt_used.get(site, (now, 0))
    if now - start >= _config["preempt_window"]:
        _preempt_used[site] = (now, 1)
    else:
        _preempt_used[site] = (start, count + 1)


def _sweep_timeouts(now):
    max_wait = _config["max_wait"]
    if not max_wait:
        return
    keep = deque()
    for rec in _queue:
        if now - rec.enqueued > max_wait:
            if _config["timeout_action"] == "drop":
                _stats["dropped"] += 1
                _log.append(_entry(rec, now, "timeout_drop"))
            else:
                _degraded.append(rec)
                _stats["degraded"] += 1
                _log.append(_entry(rec, now, "timeout_degrade"))
        else:
            keep.append(rec)
    if len(keep) != len(_queue):
        _queue.clear()
        _queue.extend(keep)


def _entry(rec, now, reason):
    return {
        "id": rec.task.get("id"),
        "site": _site(rec),
        "tenant": _tenant(rec),
        "priority": _priority(rec),
        "wait": max(0.0, now - rec.enqueued),
        "boost": _boost(rec, now),
        "reason": reason,
    }


def _commit(rec, reason, now):
    global _last_site, _last_tenant, _site_run, _tenant_run, _since_guarantee
    wait = max(0.0, now - rec.enqueued)
    boost = _boost(rec, now)
    site, tenant = _site(rec), _tenant(rec)
    if site == _last_site:
        _site_run += 1
    else:
        _last_site, _site_run = site, 1
    if tenant == _last_tenant:
        _tenant_run += 1
    else:
        _last_tenant, _tenant_run = tenant, 1
    _since_guarantee = 0 if reason == "guarantee" else _since_guarantee + 1
    if reason in ("preempt", "aging_preempt"):
        _consume_preempt(site, now)
    s = _stats
    s["popped"] += 1
    if reason == "preempt":
        s["preempted"] += 1
    elif reason == "aging_preempt":
        s["aging_preempted"] += 1
    elif reason == "guarantee":
        s["guaranteed"] += 1
    elif reason == "quota_skip":
        s["quota_skipped"] += 1
    if boost > 0:
        s["boosted"] += 1
    s["by_site"][site] = s["by_site"].get(site, 0) + 1
    s["by_tenant"][tenant] = s["by_tenant"].get(tenant, 0) + 1
    s["total_wait"] += wait
    s["max_wait"] = max(s["max_wait"], wait)
    _log.append(_entry(rec, now, reason))
    result = dict(rec.task)
    result["dequeue"] = {"reason": reason, "wait": wait, "boost": boost}
    return result
