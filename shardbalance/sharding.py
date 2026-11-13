"""基于一致性哈希的跨机房站点分片。

站点经虚拟节点落到哈希环上的执行机，扩缩容时只有环上归属发生变化
的站点需要迁移；再平衡会先等这些站点的在跑任务收尾，再原子切换
分配表，保证同一站点同一时刻只归一台机器。
"""

import bisect
import hashlib
import threading
import time

WORKERS = ["m1", "m2", "m3"]
SITES = ["shop", "news", "blog", "forum", "video", "mall", "wiki", "map"]

VIRTUAL_NODES = 128


class ShardBalanceError(Exception):
    """分片模块所有显式错误的基类。"""


class DuplicateExecutionError(ShardBalanceError):
    """同一站点已在另一台机器上执行，重复执行被拒绝。"""


class DrainTimeoutError(ShardBalanceError):
    """等待迁移站点在跑任务收尾超时，分配表保持不变。"""

    def __init__(self, message, pending_tasks, migrations):
        super().__init__(message)
        self.pending_tasks = pending_tasks
        self.migrations = migrations


class RebalanceResult(dict):
    """再平衡结果：本身是站点到机器的分配表，另带迁移信息。"""

    def __init__(self, mapping, migrations, affected_sites, pending_tasks):
        super().__init__(mapping)
        self.migrations = migrations
        self.affected_sites = affected_sites
        self.pending_tasks = pending_tasks

    def __repr__(self):
        return (
            "RebalanceResult(assignments={}, affected_sites={}, "
            "migrations={}, pending_tasks={})"
        ).format(
            dict(self),
            self.affected_sites,
            self.migrations,
            self.pending_tasks,
        )


def _hash(key):
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class _Ring:
    """每台执行机注册若干虚拟节点的一致性哈希环。"""

    def __init__(self, workers, virtual_nodes=VIRTUAL_NODES):
        points = []
        for worker in workers:
            for index in range(virtual_nodes):
                points.append((_hash("{}#{}".format(worker, index)), worker))
        points.sort(key=lambda item: item[0])
        self._points = points
        self._keys = [point for point, _ in points]

    def owner(self, site):
        if not self._points:
            raise ValueError("workers 不能为空")
        position = bisect.bisect_right(self._keys, _hash(site)) % len(self._points)
        return self._points[position][1]


def _validate_workers(workers):
    if not isinstance(workers, (list, tuple)):
        raise TypeError("workers 必须是列表或元组")
    if not workers:
        raise ValueError("workers 不能为空")
    for worker in workers:
        if not isinstance(worker, str) or not worker:
            raise ValueError("worker 名称必须是非空字符串")
    if len(set(workers)) != len(workers):
        raise ValueError("workers 存在重复机器: {}".format(workers))
    return list(workers)


def _validate_site(site):
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串")
    return site


_condition = threading.Condition(threading.RLock())
_known_sites = list(SITES)
_table = {}
_running = {}
_ring_cache = {}


def _get_ring(workers):
    key = tuple(workers)
    ring = _ring_cache.get(key)
    if ring is None:
        ring = _Ring(workers)
        _ring_cache[key] = ring
    return ring


def _reset_state():
    """恢复初始状态，仅供测试使用。"""
    with _condition:
        _known_sites[:] = list(SITES)
        _running.clear()
        _ring_cache.clear()
        _table.clear()
        ring = _get_ring(list(WORKERS))
        _table.update({site: ring.owner(site) for site in _known_sites})


_reset_state()


def assign(site, workers=None):
    """返回站点当前归属的机器。

    不传 workers 时读取再平衡维护的分配表（默认机器集合）；传入
    workers 时按给定机器集合计算一致性哈希归属，不改写分配表。
    """
    site = _validate_site(site)
    if workers is None:
        with _condition:
            if site not in _known_sites:
                _known_sites.append(site)
                _table[site] = _get_ring(list(WORKERS)).owner(site)
            return _table[site]
    pool = _validate_workers(workers)
    return _get_ring(pool).owner(site)


def acquire(site, worker):
    """登记某台机器开始跑某站点，重复执行会被拒绝。"""
    site = _validate_site(site)
    if not isinstance(worker, str) or not worker:
        raise ValueError("worker 名称必须是非空字符串")
    with _condition:
        owner = assign(site)
        if worker != owner:
            raise ShardBalanceError(
                "机器 {} 不是站点 {} 的归属机器（归属 {})".format(worker, site, owner)
            )
        if site in _running:
            current = _running[site]
            raise DuplicateExecutionError(
                "站点 {} 已在机器 {} 上执行，拒绝机器 {} 的重复执行".format(
                    site, current, worker
                )
            )
        _running[site] = worker


def release(site, worker):
    """登记某台机器上的站点任务收尾，并唤醒等待中的再平衡。"""
    site = _validate_site(site)
    if not isinstance(worker, str) or not worker:
        raise ValueError("worker 名称必须是非空字符串")
    with _condition:
        if site not in _running:
            raise ShardBalanceError("站点 {} 当前没有在跑任务".format(site))
        if _running[site] != worker:
            raise ShardBalanceError(
                "站点 {} 由机器 {} 执行，机器 {} 无权收尾".format(
                    site, _running[site], worker
                )
            )
        del _running[site]
        _condition.notify_all()


def running_tasks():
    """返回在跑任务的快照：站点到执行机。"""
    with _condition:
        return dict(_running)


def rebalance(workers=None, timeout=None):
    """按新机器集合再平衡，先等迁移站点的在跑任务收尾再切换分配表。

    返回 RebalanceResult：分配表、迁移清单、受影响站点数和未收尾任务。
    timeout 为 None 时一直等待；超时则抛 DrainTimeoutError，分配表不变。
    """
    pool = _validate_workers(list(WORKERS) if workers is None else workers)
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout < 0):
        raise ValueError("timeout 必须是非负数值或 None")

    with _condition:
        ring = _get_ring(pool)
        new_table = {site: ring.owner(site) for site in _known_sites}
        migrations = []
        for site in _known_sites:
            old = _table.get(site)
            if old is not None and old != new_table[site]:
                migrations.append((site, old, new_table[site]))
        affected = {site for site, _, _ in migrations}

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            pending = {
                site: worker
                for site, worker in _running.items()
                if site in affected
            }
            if not pending:
                break
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DrainTimeoutError(
                        "等待 {} 个迁移站点任务收尾超时".format(len(pending)),
                        pending_tasks=pending,
                        migrations=migrations,
                    )
                _condition.wait(remaining)
            else:
                _condition.wait()

        _table.clear()
        _table.update(new_table)
        return RebalanceResult(
            mapping=dict(new_table),
            migrations=migrations,
            affected_sites=len(affected),
            pending_tasks={},
        )
