"""多批次并发协调：同一业务键全局只允许一个提交在跑。"""
import threading
from contextlib import contextmanager


class KeyCoordinator:
    """每个业务键一把锁；抢不到锁的调用方进等待队列阻塞等待。

    进程内所有 submit_batch 共享同一个实例时即可跨批次去重。
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._key_locks = {}
        self._inflight = set()
        self._waiters = {}
        self._total_waits = 0

    @contextmanager
    def lease(self, business_key):
        with self._guard:
            key_lock = self._key_locks.get(business_key)
            if key_lock is None:
                key_lock = threading.Lock()
                self._key_locks[business_key] = key_lock
            if key_lock.locked():
                self._waiters[business_key] = self._waiters.get(business_key, 0) + 1
                self._total_waits += 1
                queued = True
            else:
                queued = False
        key_lock.acquire()
        try:
            with self._guard:
                if queued:
                    self._waiters[business_key] -= 1
                self._inflight.add(business_key)
            yield
        finally:
            with self._guard:
                self._inflight.discard(business_key)
            key_lock.release()
            with self._guard:
                if (
                    business_key in self._key_locks
                    and not self._key_locks[business_key].locked()
                    and self._waiters.get(business_key, 0) == 0
                ):
                    self._key_locks.pop(business_key, None)
                    self._waiters.pop(business_key, None)

    def stats(self):
        with self._guard:
            return {
                "inflight": sorted(self._inflight),
                "waiting": dict(self._waiters),
                "total_waits": self._total_waits,
            }
