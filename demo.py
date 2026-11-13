from ratelimit import Limiter

ticks = []


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        ticks.append(self.now)
        return self.now

    def sleep(self, seconds):
        self.now += seconds


clock = Clock()
limiter = Limiter(interval=1.5, clock=clock, sleep=clock.sleep)
for _ in range(3):
    limiter.acquire("shop")
limiter.report("shop", 429)
print("请求时刻", ticks)
print("上报", limiter.reports)

# TODO: 第二段演示（实现后补）
