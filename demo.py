"""模拟大站占满额度：站点隔离、优先级抢占、超时回收。"""

import importlib
import time

from siteleasectl import (
    checkpoint_of,
    lease,
    register_checkpoint,
    release,
    usage,
)

ctl = importlib.import_module("siteleasectl.lease")

print("== 大站 shop 试图吃光额度（每站上限 %d） ==" % ctl.SITE_LIMIT)
granted = []
for _ in range(3):
    try:
        granted.append(lease("shop", priority=3)["id"])
    except RuntimeError as exc:
        print("shop 第三个请求被拒:", exc)
print("shop 占用:", usage()["sites"]["shop"])

print("\n== 站点隔离：news 仍能拿到租约 ==")
news = lease("news")
print("news 拿到", news["id"], "，全局占用", usage()["used"], "/", usage()["limit"])

print("\n== 释放一个 shop 租约，等待队列里的请求被补位 ==")
release(granted[0])
print("等待队列:", usage()["waiting"], "，已补位:", usage()["promoted"])

print("\n== 高优抢占低优，被抢占任务落检查点 ==")
victim = usage()["sites"]["shop"]["leases"][0]
register_checkpoint(victim, lambda rec: {"task": rec["owner"], "progress": 42})
high = lease("shop", priority=9)
print("高优租约", high["id"], "抢占", victim, "，检查点:", checkpoint_of(victim))
print("抢占记录:", usage()["preemptions"])

print("\n== 租期到期自动回收 ==")
release(news["id"])
ctl.LEASE_TTL = 0.2
stale = lease("blog")
time.sleep(0.3)
print("blog 租约", stale["id"], "已过期，全局占用回落为", usage()["used"])

release(high["id"])
print("\n最终状态:", usage())
