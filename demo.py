"""blockvote 演示：多信号投票、误判加白与探针恢复（用虚拟时钟即时运行）。"""

import logging

from blockvote import block
from blockvote import observe, allow, votes, status, add_whitelist

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


clock = FakeClock()
block._now = clock
block._blocked.clear()


def show(site):
    total, breakdown = votes(site)
    print(f"  [{clock.now:6.0f}s] {site}: state={status(site)} "
          f"allow={allow(site)} votes={total} {breakdown}")


print("== 场景一：弱信号多信号投票，过阈才熔断 ==")
observe("shop", "captcha")
observe("shop", "redirect")
show("shop")
observe("shop", "ratelimit")
show("shop")

print("== 场景二：403 误判，人工加白临时放行，过期后探针恢复 ==")
observe("news", "403")
show("news")
add_whitelist("news", ttl_seconds=120)
observe("news", "403")  # 白名单期内信号被旁路，不累计、不熔断
show("news")
clock.advance(121)       # 白名单过期，底层仍为熔断态
show("news")

clock.advance(block.COOLDOWN_SECONDS)
for step in range(block.RECOVERY_SUCCESSES):
    observe("news", "200")  # 低频探针：首个信号进入观察态
    print(f"  probe {step + 1}: {block.stats('news')['probe_successes']} "
          f"successes, state={status('news')}")
    clock.advance(block.PROBE_INTERVAL_SECONDS)
show("news")

print("== 事件流（状态迁移 / 探针 / 白名单）==")
for event in block.events("news"):
    if event["kind"] in ("state_transition", "probe_ok", "probe_skipped",
                          "whitelist_added", "whitelist_expired"):
        print(" ", event)
