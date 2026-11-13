"""模拟快慢两个站点：慢站点频繁 429，限流器自动降速，稳定后缓慢回升。"""
from ratelimit import Limiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Site:
    """模拟站点：请求间隔小于 limit 秒就返回 429。"""

    def __init__(self, clock, limit):
        self.clock = clock
        self.limit = limit
        self.last = None

    def fetch(self):
        now = self.clock()
        ok = self.last is None or now - self.last >= self.limit
        self.last = now
        return 200 if ok else 429


clock = Clock()
limiter = Limiter(interval=0.5, recover_after=3, clock=clock, sleep=clock.sleep)
sites = {"fast": Site(clock, 0.5), "slow": Site(clock, 2.0)}

for _ in range(12):
    for name, site in sites.items():
        limiter.acquire(name)
        limiter.report(name, site.fetch())

print(f"模拟时长 {clock.now:.1f}s")
for name, stats in limiter.stats().items():
    print(f"{name}: 当前间隔 {stats['interval']:.2f}s, "
          f"请求 {stats['requests']} 次, 被限 {stats['throttled']} 次, "
          f"总等待 {stats['wait_total']:.1f}s")
