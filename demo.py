"""演示：大站占满额度 -> 站点隔离 -> 优先级抢占 -> 超时回收与幂等释放。"""

import time

from siteleasectl import (
    LeaseLimitError,
    checkpoint,
    is_active,
    lease,
    preemptions,
    reclaim_expired,
    release,
    reset,
    set_site_limit,
    status,
    usage,
    wait_queue,
)

for site in ("shop", "shop", "news"):
    lease(site)
print(usage())
release("lease-1")
print(usage())

print("--- 大站无站点上限时吃光全局额度 ---")
reset()
for _ in range(3):
    lease("shop", priority=3)
try:
    lease("news", priority=3)
except LeaseLimitError as exc:
    print("news 被拒:", exc)
print("等待队列:", wait_queue())

print("--- 站点上限隔离大站 ---")
reset()
set_site_limit("shop", 1)
slow = lease("shop", priority=3)
try:
    lease("shop", priority=3)
except LeaseLimitError as exc:
    print("shop 第二路被拒:", exc)
lease("news", priority=3)
lease("blog", priority=3)
print("各站占用:", status()["sites"])

print("--- 高优抢占，被抢占任务检查点保留 ---")
checkpoint(slow["id"], {"page": 12, "done": False})
urgent = lease("news", priority=9)
print("抢占记录:", preemptions())
print("被抢占租约仍有效?", is_active(slow["id"]))

print("--- 超时回收与幂等释放 ---")
release(urgent["id"])
temp = lease("blog", ttl=0.05)
time.sleep(0.08)
print("过期回收:", reclaim_expired())
print("回收后再释放(幂等):", release(temp["id"]))
keep = lease("blog")
print("首次释放:", release(keep["id"]), "重复释放:", release(keep["id"]))
print("最终状态:", status())
