"""两个站点、多会话的出口池演示：粘性保持、失效切换、冷却恢复、站点隔离。"""
import random

from proxysticky import PoolExhaustedError, StickyPool


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        print("-- 时钟前进 %ds（t=%ds）" % (seconds, self.t))


clock = FakeClock()
pool = StickyPool(
    ["10.0.0.11:8080", "10.0.0.12:8080", "10.0.0.13:8080"],
    sticky_window=30, failure_threshold=2, cooldown=10,
    time_func=clock, rng=random.Random(7),
)

print("== 1. 粘性保持：shop/s1 窗口内连续 3 次请求 ==")
for step in range(3):
    print("step", step, pool.acquire("shop", "s1"))

print("== 2. 站点隔离：news/s1 不会占用 shop/s1 的出口 ==")
print("news/s1 ->", pool.acquire("news", "s1"))
print("shop/s2 ->", pool.acquire("shop", "s2"))

print("== 3. 失效切换：shop/s1 的出口连续失败 2 次被摘除 ==")
bound = pool.bindings()["shop"]["s1"]["proxy"]
print("shop/s1 当前出口:", bound)
print("第 1 次失败，触发摘除:", pool.report_failure("shop", "s1", bound))
print("第 2 次失败，触发摘除:", pool.report_failure("shop", "s1", bound))
print("shop/s1 重选 ->", pool.acquire("shop", "s1"))

print("== 4. 冷却期间单出口站点无出口可用 ==")
solo = StickyPool(["10.0.0.21:8080"], sticky_window=30,
                  failure_threshold=1, cooldown=10,
                  time_func=clock, rng=random.Random(7))
print("api/s9 ->", solo.acquire("api", "s9"))
solo.report_failure("api", "s9", "10.0.0.21:8080")
try:
    solo.acquire("api", "s9")
except PoolExhaustedError as exc:
    print("api/s9 取出口失败:", exc)

print("== 5. 冷却恢复 + 会话过期重选 ==")
clock.advance(11)
print("api/s9 冷却恢复后 ->", solo.acquire("api", "s9"))
clock.advance(31)
print("shop/s1 窗口过期重选 ->", pool.acquire("shop", "s1"))

print("== 当前绑定 ==")
for site, sessions in sorted(pool.bindings().items()):
    for session, info in sorted(sessions.items()):
        print(site, session, info["proxy"], "reason=%s" % info["reason"])

print("== 切换原因 ==")
for event in pool.switch_log():
    print(event["site"], event["session"], "->", event["proxy"],
          "reason=%s" % event["reason"])

print("== 池状态 ==")
print(pool.status())
