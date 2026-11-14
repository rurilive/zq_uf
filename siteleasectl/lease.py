"""按站点的并发租约：每站独立上限、租期过期回收、优先级抢占、检查点。"""

import atexit
import itertools
import sys
import time
import types

GLOBAL_LIMIT = 3   # 全局总额度（保留旧口径）
SITE_LIMIT = 2     # 每个站点独立的并发上限
LEASE_TTL = 30.0   # 租约租期（秒），过期自动回收

_now = time.monotonic  # 测试可替换的时钟

_used = 0
_seq = 0
_request_seq = 0
_owner_seq = itertools.count(1)
_leases = {}               # lease_id -> 租约记录
_waiters = []              # 等待队列（被暂缓的请求）
_promoted = []             # 已从等待队列补位的记录
_preemptions = []          # 抢占记录
_checkpoints = {}          # lease_id -> 检查点内容
_checkpoint_handlers = {}  # lease_id -> 检查点回调


def _reset_state():
    global _used, _seq, _request_seq
    _used = 0
    _seq = 0
    _request_seq = 0
    _leases.clear()
    _waiters.clear()
    _promoted.clear()
    _preemptions.clear()
    _checkpoints.clear()
    _checkpoint_handlers.clear()


def _inc_used():
    global _used
    _used += 1


def _dec_used():
    global _used
    _used = max(0, _used - 1)


def _site_used(site):
    return sum(1 for r in _leases.values()
               if r["site"] == site and r["state"] == "active")


def _reap_expired():
    """惰性回收：任何 API 调用前先把过期租约释放掉。"""
    now = _now()
    for rec in list(_leases.values()):
        if rec["state"] == "active" and rec["expires_at"] <= now:
            rec["state"] = "expired"
            _dec_used()
    _promote_waiters()


def _grant(site, priority):
    global _seq
    _seq += 1
    now = _now()
    rec = {
        "id": "lease-%d" % _seq,
        "site": site,
        "priority": priority,
        "owner": "task-%d" % next(_owner_seq),  # 同一租约同一时刻只属于一个任务
        "state": "active",
        "granted_at": now,
        "expires_at": now + LEASE_TTL,
    }
    _leases[rec["id"]] = rec
    _inc_used()
    return {k: rec[k] for k in ("id", "site", "priority", "owner", "expires_at")}


def _pick_victim(site, priority):
    """站点满则在本站找牺牲品，否则全局满时在全局找；只抢优先级严格更低的。"""
    if _site_used(site) >= SITE_LIMIT:
        pool = [r for r in _leases.values()
                if r["state"] == "active" and r["site"] == site]
    elif _used >= GLOBAL_LIMIT:
        pool = [r for r in _leases.values() if r["state"] == "active"]
    else:
        return None
    pool = [r for r in pool if r["priority"] < priority]
    if not pool:
        return None
    return min(pool, key=lambda r: (r["priority"], r["granted_at"]))


def _preempt(victim, new_priority):
    handler = _checkpoint_handlers.get(victim["id"])
    payload = handler(dict(victim)) if handler is not None else None
    _checkpoints[victim["id"]] = payload
    victim["state"] = "preempted"
    _dec_used()
    _preemptions.append({
        "lease_id": victim["id"],
        "site": victim["site"],
        "owner": victim["owner"],
        "preempted_by_priority": new_priority,
        "checkpoint": payload,
        "at": _now(),
    })


def _enqueue(site, priority):
    global _request_seq
    _request_seq += 1
    req = {
        "request": "req-%d" % _request_seq,
        "site": site,
        "priority": priority,
        "enqueued_at": _now(),
    }
    _waiters.append(req)
    return req


def _promote_waiters():
    """额度空出后，按优先级从高到低给等待队列补位。"""
    for req in sorted(list(_waiters),
                      key=lambda r: (-r["priority"], r["enqueued_at"])):
        if _site_used(req["site"]) < SITE_LIMIT and _used < GLOBAL_LIMIT:
            _waiters.remove(req)
            rec = _grant(req["site"], req["priority"])
            _promoted.append({"request": req["request"],
                              "lease_id": rec["id"], "site": req["site"]})


def lease(site, priority=5):
    """申请站点租约；额度不足且无法抢占时入等待队列并抛 RuntimeError。"""
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串: %r" % (site,))
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError("priority 必须是整数: %r" % (priority,))
    _reap_expired()
    if _site_used(site) < SITE_LIMIT and _used < GLOBAL_LIMIT:
        return _grant(site, priority)
    victim = _pick_victim(site, priority)
    if victim is not None:
        _preempt(victim, priority)
        return _grant(site, priority)
    req = _enqueue(site, priority)
    raise RuntimeError(
        "站点 %r 并发额度不足（站点上限 %d，全局上限 %d），"
        "请求 %s 已加入等待队列" % (site, SITE_LIMIT, GLOBAL_LIMIT, req["request"]))


def release(lease_id):
    """释放租约；幂等：重复释放返回 False，未知 id 抛 ValueError。"""
    _reap_expired()
    rec = _leases.get(lease_id)
    if rec is None:
        raise ValueError("未知租约 id: %r" % (lease_id,))
    if rec["state"] != "active":
        return False
    rec["state"] = "released"
    _dec_used()
    _promote_waiters()
    return True


def register_checkpoint(lease_id, handler):
    """给租约注册检查点回调，被抢占时调用并保存返回值。"""
    if lease_id not in _leases:
        raise ValueError("未知租约 id: %r" % (lease_id,))
    if not callable(handler):
        raise ValueError("checkpoint handler 必须可调用: %r" % (handler,))
    _checkpoint_handlers[lease_id] = handler
    return True


def checkpoint_of(lease_id):
    """读取被抢占租约落下的检查点，未落过则返回 None。"""
    if lease_id not in _leases:
        raise ValueError("未知租约 id: %r" % (lease_id,))
    return _checkpoints.get(lease_id)


def usage():
    """输出全局与各站占用、等待队列和抢占记录。"""
    _reap_expired()
    sites = {}
    for rec in _leases.values():
        if rec["state"] != "active":
            continue
        slot = sites.setdefault(rec["site"],
                                {"used": 0, "limit": SITE_LIMIT, "leases": []})
        slot["used"] += 1
        slot["leases"].append(rec["id"])
    return {
        "used": _used,
        "limit": GLOBAL_LIMIT,
        "site_limit": SITE_LIMIT,
        "ttl": LEASE_TTL,
        "sites": sites,
        "waiting": [dict(w) for w in _waiters],
        "promoted": list(_promoted),
        "preemptions": list(_preemptions),
    }


def _shutdown():
    """进程退出时回收全部租约，避免额度泄漏。"""
    global _used
    for rec in _leases.values():
        if rec["state"] == "active":
            rec["state"] = "released"
    _used = 0


atexit.register(_shutdown)


class _LeaseModule(types.ModuleType):
    def __setattr__(self, name, value):
        # 测试钩子：外部把 _used 清零时，连带清空全部租约状态
        if name == "_used" and not value:
            _reset_state()
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _LeaseModule
