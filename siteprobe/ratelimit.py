"""单站点限速器：并发请求共享同一最小间隔。"""

import threading
import time


class RateLimiter:
    """按固定间隔放行请求；rate_per_sec<=0 时不限速。"""

    def __init__(self, rate_per_sec=0.0):
        self.interval = 1.0 / rate_per_sec if rate_per_sec and rate_per_sec > 0 else 0.0
        self.requests = 0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self):
        if self.interval <= 0:
            with self._lock:
                self.requests += 1
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait <= 0:
                self._next_at = now + self.interval
                self.requests += 1
                return
            self._next_at += self.interval
            self.requests += 1
        time.sleep(max(0.0, wait))
