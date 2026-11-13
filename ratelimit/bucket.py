"""按站点自适应的令牌桶节流。"""
import threading
import time


class _Bucket:
    """单个站点的令牌桶与自适应状态。"""

    __slots__ = ("base_interval", "interval", "capacity", "tokens", "updated",
                 "successes", "requests", "wait_total", "throttled")

    def __init__(self, interval, capacity, now):
        self.base_interval = interval
        self.interval = interval
        self.capacity = capacity
        self.tokens = capacity
        self.updated = now
        self.successes = 0
        self.requests = 0
        self.wait_total = 0.0
        self.throttled = 0


class _Condition:
    """条件变量：等待由注入的 sleep 驱动，notify_all 可提前唤醒真实等待者。"""

    def __init__(self, lock, sleep):
        self._lock = lock
        self._sleep = sleep
        self._events = []

    def wait(self, timeout):
        event = threading.Event()
        self._events.append(event)
        self._lock.release()
        try:
            if self._sleep is time.sleep:
                event.wait(timeout)
            else:
                self._sleep(timeout)
        finally:
            self._lock.acquire()
            self._events.remove(event)

    def notify_all(self):
        for event in self._events:
            event.set()


class Limiter:
    """每个站点一个令牌桶，按反馈自动降速和回升。

    interval: 默认每请求间隔（秒）；burst: 桶容量（突发上限）。
    sites: 按站点覆盖配置，如 {"slow": {"interval": 2.0, "burst": 1}}。
    429/5xx 时间隔乘 backoff_factor（上限 max_interval）；
    连续 recover_after 次成功，间隔除 recover_factor，最低回到初始 interval。
    """

    def __init__(self, interval=1.0, burst=1, sites=None, max_interval=60.0,
                 backoff_factor=2.0, recover_factor=1.5, recover_after=5,
                 clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.burst = burst
        self.max_interval = max_interval
        self.backoff_factor = backoff_factor
        self.recover_factor = recover_factor
        self.recover_after = recover_after
        self.clock = clock
        self.sleep = sleep
        self.last = {}
        self.reports = []
        self._site_config = {name: dict(cfg) for name, cfg in (sites or {}).items()}
        self._buckets = {}
        self._lock = threading.Lock()
        self._cond = _Condition(self._lock, sleep)

    def acquire(self, site):
        with self._lock:
            bucket = self._bucket_for(site)
            start = self.clock()
            while True:
                now = self.clock()
                self._refill(bucket, now)
                if bucket.tokens >= 1.0 - 1e-9:
                    bucket.tokens = max(0.0, bucket.tokens - 1.0)
                    break
                need = (1.0 - bucket.tokens) * bucket.interval
                self._cond.wait(need)
            now = self.clock()
            bucket.wait_total += now - start
            bucket.requests += 1
            self.last[site] = now

    def report(self, site, status):
        with self._lock:
            self.reports.append((site, status))
            bucket = self._bucket_for(site)
            if status == 429 or 500 <= status < 600:
                bucket.throttled += 1
                bucket.successes = 0
                bucket.interval = min(bucket.interval * self.backoff_factor,
                                      self.max_interval)
                self._cond.notify_all()
            elif 200 <= status < 400:
                bucket.successes += 1
                if (bucket.successes >= self.recover_after
                        and bucket.interval > bucket.base_interval):
                    bucket.interval = max(bucket.interval / self.recover_factor,
                                          bucket.base_interval)
                    bucket.successes = 0
                    self._cond.notify_all()

    def configure(self, site, interval=None, burst=None):
        """运行时调整单个站点的速率或突发。"""
        with self._lock:
            cfg = self._site_config.setdefault(site, {})
            if interval is not None:
                cfg["interval"] = interval
            if burst is not None:
                cfg["burst"] = burst
            bucket = self._buckets.get(site)
            if bucket is not None:
                if interval is not None:
                    bucket.base_interval = float(interval)
                    bucket.interval = min(max(bucket.interval, bucket.base_interval),
                                          self.max_interval)
                if burst is not None:
                    bucket.capacity = float(burst)
                    bucket.tokens = min(bucket.tokens, bucket.capacity)
            self._cond.notify_all()

    def stats(self, site=None):
        """站点统计：requests、wait_total（总等待秒数）、throttled（被限次数）、interval。"""
        with self._lock:
            if site is not None:
                return self._snapshot(self._bucket_for(site))
            return {name: self._snapshot(bucket)
                    for name, bucket in self._buckets.items()}

    def _snapshot(self, bucket):
        return {
            "requests": bucket.requests,
            "wait_total": bucket.wait_total,
            "throttled": bucket.throttled,
            "interval": bucket.interval,
        }

    def _bucket_for(self, site):
        bucket = self._buckets.get(site)
        if bucket is None:
            cfg = self._site_config.get(site, {})
            interval = float(cfg.get("interval", self.interval))
            capacity = float(cfg.get("burst", self.burst))
            bucket = _Bucket(interval, capacity, self.clock())
            self._buckets[site] = bucket
        return bucket

    def _refill(self, bucket, now):
        elapsed = now - bucket.updated
        if elapsed > 0:
            bucket.tokens = min(bucket.capacity,
                                bucket.tokens + elapsed / bucket.interval)
            bucket.updated = now
