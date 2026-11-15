"""模拟站点抖动：两个任务共享 shop 的阶段状态，news 平稳对照。"""

import logging
import random
import threading
import time

import crawlcap.cap as cap
from crawlcap import acquire, report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 演示用小参数，方便在几秒内看到爬升/回退/排队/回收
cap._COOLDOWN_SECS = 0.5
cap._LEASE_TTL = 0.4
cap._QUOTA_CAPACITY = 6
cap._QUOTA_RATE = 3.0

random.seed(7)


def task(name, site, n, jitter):
    for _ in range(n):
        g = acquire(site)
        if not g["ok"]:
            time.sleep(min(g.get("wait", 0.05), 0.2))
            continue
        ok = random.random() > jitter
        rtt = random.uniform(0.05, 0.3) if ok else random.uniform(0.5, 2.5)
        time.sleep(min(rtt, 0.05))
        print(name, report(site, ok, round(rtt, 2)))


if __name__ == "__main__":
    # shop 抖动大（40% 失败），两个任务并发打同一站点，共享阶段状态
    threads = [
        threading.Thread(target=task, args=("task-A", "shop", 30, 0.4)),
        threading.Thread(target=task, args=("task-B", "shop", 30, 0.4)),
        threading.Thread(target=task, args=("task-C", "news", 20, 0.02)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("done")
