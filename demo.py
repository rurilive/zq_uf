import threading
import time

from shardbalance import (
    SITES,
    acquire,
    assign,
    rebalance,
    release,
    running_tasks,
)

before = rebalance(["m1", "m2", "m3"])
print("下线前分配:", dict(before))

# 模拟 m3 上两个站点的采集任务正在跑
busy = [site for site in SITES if before[site] == "m3"][:2]
for site in busy:
    acquire(site, "m3")
print("在跑任务:", running_tasks())

# 同一站点重复执行会被明确拒绝
try:
    acquire(busy[0], "m3")
except Exception as exc:
    print("重复执行被拒绝:", exc)

finished = threading.Event()
holder = {}

def do_rebalance():
    # m3 下线：再平衡等待迁移站点任务收尾后才切换分配表
    holder["result"] = rebalance(["m1", "m2"], timeout=30)
    finished.set()

thread = threading.Thread(target=do_rebalance)
thread.start()
time.sleep(0.2)
print("收尾前分配表未变:", assign(busy[0]) == "m3")

for site in busy:
    release(site, "m3")
finished.wait(5)
thread.join()

result = holder["result"]
print("迁移清单:", result.migrations)
print("受影响站点数:", result.affected_sites)
print("未收尾任务:", result.pending_tasks)
print("下线后分配:", dict(result))
