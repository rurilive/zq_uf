"""按站点的并发租约：站点独立上限、优先级抢占、过期回收与检查点。"""

import threading
import time

LIMIT = 3
DEFAULT_TTL = 30.0

_used = 0
_seq = 0
_leases = {}
_site_limits = {}
_waiting = []
_preemptions = []
_checkpoints = {}
_lock = threading.RLock()


class LeaseError(RuntimeError):
    """租约模块错误基类。"""


class LeaseLimitError(LeaseError):
    """容量不足且没有可抢占的低优先级租约。"""


class LeaseStateError(LeaseError):
    """租约状态不允许当前操作（如给已失效租约落检查点）。"""


def _now():
    return time.monotonic()


def _site_used(site):
    return sum(1 for rec in _leases.values() if rec["site"] == site)


def _site_full(site):
    limit = _site_limits.get(site)
    return limit is not None and _site_used(site) >= limit


def _reclaim(lease_id):
    """从活动集合移除租约并归还全局额度，幂等。调用方需持锁。"""
    global _used
    if _leases.pop(lease_id, None) is None:
        return False
    _used = max(0, _used - 1)
    return True


def _sweep(now):
    """回收所有过期租约，返回回收的 id 列表。调用方需持锁。"""
    expired = [lid for lid, rec in _leases.items() if rec["expires_at"] <= now]
    for lid in expired:
        _reclaim(lid)
    return expired


def _grant(site, priority, ttl, owner, now):
    global _used, _seq
    _seq += 1
    rec = {
        "id": "lease-%d" % _seq,
        "site": site,
        "priority": priority,
        "owner": owner,
        "granted_at": now,
        "expires_at": now + ttl,
        "ttl": ttl,
    }
    _leases[rec["id"]] = rec
    _used += 1
    return rec


def _pick_victim(site, priority):
    """选严格更低优先级的牺牲品：站点满只在站内选，否则在全局选。调用方需持锁。"""
    same_site_only = _site_full(site)
    candidates = [
        rec for rec in _leases.values()
        if rec["priority"] < priority and (not same_site_only or rec["site"] == site)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda rec: (rec["priority"], rec["granted_at"]))


def lease(site, priority=5, ttl=None, owner=None):
    """申请站点租约。

    容量充足直接授予；容量满时尝试抢占严格更低优先级的租约（被抢占方的
    检查点随抢占记录保留）；无法抢占则把请求记入等待队列并抛 LeaseLimitError。
    """
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串: %r" % (site,))
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError("priority 必须是整数: %r" % (priority,))
    if ttl is not None and (not isinstance(ttl, (int, float)) or ttl <= 0):
        raise ValueError("ttl 必须是正数秒: %r" % (ttl,))
    with _lock:
        now = _now()
        _sweep(now)
        ttl = DEFAULT_TTL if ttl is None else ttl
        if _used < LIMIT and not _site_full(site):
            return dict(_grant(site, priority, ttl, owner, now))
        victim = _pick_victim(site, priority)
        if victim is not None:
            saved = _checkpoints.get(victim["id"])
            _reclaim(victim["id"])
            rec = _grant(site, priority, ttl, owner, now)
            _preemptions.append({
                "lease_id": victim["id"],
                "site": victim["site"],
                "priority": victim["priority"],
                "by_id": rec["id"],
                "by_priority": priority,
                "checkpoint": saved,
                "at": now,
            })
            return dict(rec)
        reason = "站点 %r 并发已满（站点 %d/%s，全局 %d/%d），且无更低优先级租约可抢占" % (
            site, _site_used(site), _site_limits.get(site, "-"), _used, LIMIT)
        _waiting.append({"site": site, "priority": priority, "at": now, "reason": reason})
        raise LeaseLimitError(reason + "；请求已加入等待队列")


def release(lease_id):
    """释放租约。幂等：已释放/已过期/不存在时返回 False，不重复扣额度。"""
    with _lock:
        return _reclaim(lease_id)


def reclaim_expired():
    """回收所有过期租约，返回回收的 id 列表。幂等。"""
    with _lock:
        return _sweep(_now())


def checkpoint(lease_id, data):
    """给活动租约落检查点；租约已释放/过期/被抢占时抛 LeaseStateError。"""
    with _lock:
        _sweep(_now())
        if lease_id not in _leases:
            raise LeaseStateError("租约 %r 不存在或已失效，无法落检查点" % (lease_id,))
        _checkpoints[lease_id] = data
        return True


def is_active(lease_id):
    """租约当前是否有效。租约 id 全局唯一且不复用，同一时刻只属于一个持有者。"""
    with _lock:
        _sweep(_now())
        return lease_id in _leases


def _site_usage():
    sites = {}
    for site, limit in _site_limits.items():
        sites[site] = {"used": 0, "limit": limit}
    for rec in _leases.values():
        entry = sites.setdefault(
            rec["site"], {"used": 0, "limit": _site_limits.get(rec["site"])})
        entry["used"] += 1
    return sites


def usage():
    """全局用量与各站占用。"""
    with _lock:
        _sweep(_now())
        return {
            "used": _used,
            "limit": LIMIT,
            "sites": _site_usage(),
            "waiting": len(_waiting),
            "preemptions": len(_preemptions),
        }


def status():
    """各站占用、等待队列和抢占记录的快照。"""
    with _lock:
        _sweep(_now())
        return {
            "sites": _site_usage(),
            "waiting": [dict(item) for item in _waiting],
            "preemptions": [dict(item) for item in _preemptions],
        }


def wait_queue():
    """因容量不足被拒、正在等待容量的请求。"""
    return status()["waiting"]


def preemptions():
    """抢占记录，含被抢占租约的检查点。"""
    return status()["preemptions"]


def checkpoints():
    """所有已保存的检查点（含被抢占租约的，供其恢复进度）。"""
    with _lock:
        return dict(_checkpoints)


def set_site_limit(site, limit):
    """设置站点独立并发上限（正整数）。未设置的站点只受全局上限约束。"""
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串: %r" % (site,))
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("站点上限必须是正整数: %r" % (limit,))
    with _lock:
        _site_limits[site] = limit


def reset():
    """清空全部状态（租约、站点上限、等待队列、抢占记录、检查点）。"""
    global _used, _seq
    with _lock:
        _used = 0
        _seq = 0
        _leases.clear()
        _site_limits.clear()
        _waiting.clear()
        _preemptions.clear()
        _checkpoints.clear()
