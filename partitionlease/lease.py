"""带栅栏的分区租约：过期、单调栅栏、原子续期，纯标准库。"""

import threading
import time

_lock = threading.RLock()
_leases = {}  # (dataset, partition) -> 当前租约
_fences = {}  # (dataset, partition) -> 已发出的最大栅栏值，只增不减
_events = []  # 持有者、栅栏、抢占、回收、续期、释放、写入记录


class LeaseError(Exception):
    """租约错误基类。"""


class NotLeaseHolderError(LeaseError):
    """不是当前持有者。"""


class LeaseExpiredError(LeaseError):
    """租约已过期。"""


class StaleFenceError(LeaseError):
    """栅栏值过旧，写入被拒。"""


def _record(event, dataset, partition, owner, fence_token, **extra):
    entry = {
        "event": event,
        "dataset": dataset,
        "partition": partition,
        "owner": owner,
        "fence_token": fence_token,
        "at": time.time(),
    }
    entry.update(extra)
    _events.append(entry)


def _view(dataset, partition, lease):
    return {
        "dataset": dataset,
        "partition": partition,
        "owner": lease["owner"],
        "fence_token": lease["fence_token"],
        "expires_at": lease["expires_at"],
        "ttl": lease["ttl"],
    }


def acquire(dataset, partition, owner, ttl):
    """获取租约，返回持有者与单调栅栏值。

    挤掉他人有效租约记 preempt，接收过期租约记 reclaim；
    旧持有者此后的写入和续期都会被拒。
    """
    key = (dataset, partition)
    with _lock:
        fence = _fences.get(key, 0) + 1
        _fences[key] = fence
        prev = _leases.get(key)
        event, extra = "acquire", {}
        if prev is not None and prev["owner"] != owner:
            if prev["expires_at"] <= time.monotonic():
                event = "reclaim"
            else:
                event = "preempt"
            extra = {"prev_owner": prev["owner"], "prev_fence_token": prev["fence_token"]}
        lease = {
            "owner": owner,
            "fence_token": fence,
            "expires_at": time.monotonic() + ttl,
            "ttl": ttl,
        }
        _leases[key] = lease
        _record(event, dataset, partition, owner, fence, **extra)
        return _view(dataset, partition, lease)


def renew(dataset, partition, owner, ttl):
    """原子续期：只有当前持有者能续，返回是否成功。"""
    key = (dataset, partition)
    with _lock:
        lease = _leases.get(key)
        if lease is None or lease["owner"] != owner:
            return False
        lease["expires_at"] = time.monotonic() + ttl
        lease["ttl"] = ttl
        _record("renew", dataset, partition, owner, lease["fence_token"])
        return True


def release(dataset, partition, owner):
    """原子释放：只有持有者能释放，返回是否成功。"""
    key = (dataset, partition)
    with _lock:
        lease = _leases.get(key)
        if lease is None or lease["owner"] != owner:
            return False
        del _leases[key]
        _record("release", dataset, partition, owner, lease["fence_token"])
        return True


def write(dataset, partition, owner, fence_token, payload):
    """带栅栏写入：旧栅栏、过期租约、非持有者都被拒并记录。"""
    key = (dataset, partition)
    with _lock:
        lease = _leases.get(key)
        if lease is None:
            raise NotLeaseHolderError(f"{dataset}/{partition} 无有效租约")
        if lease["expires_at"] <= time.monotonic():
            raise LeaseExpiredError(f"{dataset}/{partition} 租约已过期")
        if fence_token != lease["fence_token"]:
            _record("write_rejected", dataset, partition, owner, fence_token,
                    reason="stale_fence", current_fence=lease["fence_token"])
            raise StaleFenceError(
                f"栅栏过旧: 携带 {fence_token}, 当前 {lease['fence_token']}")
        if lease["owner"] != owner:
            _record("write_rejected", dataset, partition, owner, fence_token,
                    reason="not_owner", current_owner=lease["owner"])
            raise NotLeaseHolderError(f"当前持有者是 {lease['owner']}")
        _record("write", dataset, partition, owner, fence_token, payload=payload)
        return _view(dataset, partition, lease)


def holder(dataset, partition):
    """当前有效租约的持有者与栅栏值；无有效租约返回 None。"""
    key = (dataset, partition)
    with _lock:
        lease = _leases.get(key)
        if lease is None or lease["expires_at"] <= time.monotonic():
            return None
        return _view(dataset, partition, lease)


def events(dataset=None, partition=None):
    """事件记录：持有者变更、抢占、回收、续期、释放、写入。"""
    with _lock:
        return [
            dict(e) for e in _events
            if dataset is None or (e["dataset"] == dataset and e["partition"] == partition)
        ]
