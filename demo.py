"""演示：会话绑定、失败轮换、冷却回切与站点隔离。

用可推进的假时钟演示，无需真实等待冷却；输出当前绑定、
轮换记录和回切记录。生产代码默认用 time.monotonic。
"""
from proxyrotate import (
    ProxyRotator,
    REASON_ALL_COOLDOWN,
    REASON_FAILURE,
    REASON_SWITCHBACK,
    acquire,
    release,
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def show(title, rotator):
    print(f"--- {title} ---")
    print("当前绑定:", rotator.bindings())
    print("冷却出口:", rotator.cooldowns())
    for event in rotator.rotations():
        print(
            f"[轮换] {event['session']}: {event['old_proxy']} -> "
            f"{event['new_proxy']} 原因={event['reason']}"
        )
    for event in rotator.switchbacks():
        print(
            f"[回切] {event['session']}: {event['old_proxy']} -> "
            f"{event['new_proxy']} 原因={event['reason']}"
        )
    print("最近一次切换原因:", rotator.last_switch_reason())
    print()


clock = FakeClock()
# 阈值/冷却/TTL 均可配：失败 2 次轮换，冷却 30s，绑定 60s
rotator = ProxyRotator(
    site="shop",
    failure_threshold=2,
    cooldown=30.0,
    session_ttl=60.0,
    clock=clock,
)

# 1. 会话绑定：同一会话跨请求出口固定，登录态不丢
proxy = rotator.acquire("session-1")
print("首次取出口:", proxy)
for _ in range(3):
    assert rotator.acquire("session-1") == proxy
print("绑定有效期内三次请求均为同一出口:", proxy, "\n")

# 2. 模拟出口连续失败，达阈值后轮换并记录原因
rotator.release("session-1", proxy, ok=False)
result = rotator.release("session-1", proxy, ok=False)
print("第二次失败上报:", result)
new_proxy = rotator.acquire("session-1")
print("轮换后新出口:", new_proxy, "\n")
show("失败轮换后", rotator)

# 3. 冷却未结束 + 绑定未到期：粘在替代出口，不回切
clock.advance(31)
print("冷却已结束但绑定未到期，仍为:", rotator.acquire("session-1"), "\n")

# 4. 绑定到期且原出口冷却结束：显式回切并记录
clock.advance(30)
switched_back = rotator.acquire("session-1")
print("绑定到期后回切到原出口:", switched_back)
show("冷却回切后", rotator)

# 5. 全部出口冷却时的兜底（阈值调成 1，依次打挂）
clock2 = FakeClock()
strict = ProxyRotator(
    site="strict", failure_threshold=1, cooldown=30.0,
    session_ttl=600.0, clock=clock2,
)
for _ in strict.proxies():
    strict.release("s", strict.acquire("s"), ok=False)
fallback = strict.acquire("s")
print("全部出口冷却时兜底返回:", fallback)
print("兜底切换原因:", strict.last_switch_reason(),
      f"(即 {REASON_ALL_COOLDOWN})\n")

# 6. 站点隔离：另一个站点的 rotator 状态完全独立
other = ProxyRotator(site="forum", clock=clock)
other_proxy = other.acquire("session-1")
print("另一站点同会话标识的独立绑定:", other.bindings())
print("该站点无冷却/无切换记录:", other.cooldowns(), other.rotations(), "\n")

# 模块级函数仍然可用（默认站点实例，签名不变）
print("模块级 acquire/release:")
print(" acquire ->", acquire("legacy-session"))
print(" release ->", release("legacy-session", acquire("legacy-session"), True))

# 原因常量便于外部按口径过滤
print("\n切换原因口径:", REASON_FAILURE, "/", REASON_SWITCHBACK, "/",
      REASON_ALL_COOLDOWN)
