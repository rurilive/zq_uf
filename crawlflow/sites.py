"""按站点分组的并发池：每个站点独立的并发上限和最小请求间隔。"""
import threading
import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class SitePolicy:
    """单个站点的调度策略。

    max_concurrency: 同时运行的该站点任务数上限。
    min_interval:    两次派发之间的最小间隔（秒），慢站点用它限速。
    """
    max_concurrency: int = 2
    min_interval: float = 0.0


class SiteTracker:
    """在内存中跟踪每个站点的运行数和上次派发时间（运行状态可恢复，限速不需要）。"""

    def __init__(self, policies=None, default_policy=None):
        self._policies = dict(policies or {})
        self._default = default_policy or SitePolicy()
        self._running = defaultdict(int)
        self._last_dispatch = {}
        self._lock = threading.Lock()

    def policy_for(self, site):
        return self._policies.get(site, self._default)

    def acquire(self, site, now=None):
        """若该站点还有额度则占一个槽位并返回 True，否则返回 False。"""
        now = time.time() if now is None else now
        policy = self.policy_for(site)
        with self._lock:
            if self._running[site] >= policy.max_concurrency:
                return False
            if now - self._last_dispatch.get(site, float("-inf")) < policy.min_interval:
                return False
            self._running[site] += 1
            self._last_dispatch[site] = now
            return True

    def release(self, site):
        with self._lock:
            if self._running[site] > 0:
                self._running[site] -= 1

    def running(self, site=None):
        with self._lock:
            if site is not None:
                return self._running[site]
            return sum(self._running.values())

    def retry_after(self, site, now=None):
        """距该站点下次可派发还剩多少秒（0 表示立即可派发）。"""
        now = time.time() if now is None else now
        policy = self.policy_for(site)
        with self._lock:
            if self._running[site] >= policy.max_concurrency:
                return float("inf")
            wait = policy.min_interval - (now - self._last_dispatch.get(site, float("-inf")))
            return max(0.0, wait)
