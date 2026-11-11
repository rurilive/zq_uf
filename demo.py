"""模拟多站点采集：演示归一化去重、分类退避、站点隔离和断点恢复。

    python3 demo.py
"""
import os
import random
import tempfile
import threading
import time
from urllib.parse import urlsplit

from crawlflow import (
    NetworkError,
    ParseError,
    PermanentError,
    Scheduler,
    SitePolicy,
)

random.seed(42)

# 模拟站点：fast 快而稳，slow 慢且 30% 概率网络超时
LATENCY = {"fast.example": 0.02, "slow.example": 0.15}
FLAKY = {"slow.example": 0.3}


def fake_fetch(url):
    site = urlsplit(url).hostname
    time.sleep(LATENCY.get(site, 0.05))
    if "missing" in url:
        raise PermanentError("404 Not Found")
    if "broken" in url:
        raise ParseError("no <article> found", sample="<html><body>ad</body></html>")
    if random.random() < FLAKY.get(site, 0.0):
        raise NetworkError("connection timeout")
    return "body of " + url


def worker(sched, done_flag):
    while True:
        task = sched.next()
        if task is None:
            if sched.pending() == 0 and sched.running() == 0:
                return
            time.sleep(0.02)  # 退避中或站点限速，稍后再试
            continue
        try:
            body = fake_fetch(task.url)
        except Exception as exc:
            sched.failure(task.url, exc)
        else:
            sched.success(task.url, body)
            # 增量游标：记录该站点已采到的位置
            sched.set_cursor(task.site, task.url)


def main():
    db_path = os.path.join(tempfile.mkdtemp(), "crawl.sqlite")

    sched = Scheduler(
        db_path,
        backoff_base=0.2,
        backoff_cap=2.0,
        sites={
            "slow.example": SitePolicy(max_concurrency=1, min_interval=0.05),
            "fast.example": SitePolicy(max_concurrency=3),
        },
    )

    # 同一篇文章的变体 URL：跟踪参数、query 顺序、编码、fragment 不同
    variants = [
        "https://fast.example/post/1?utm_source=feed&id=1",
        "https://fast.example/post/1?id=1&utm_medium=rss",
        "https://FAST.example/post/1?id=1#comments",
        "https://fast.example/post/1?id=1",
    ]
    seeds = variants + [
        "https://fast.example/post/2?id=2",
        "https://fast.example/broken",      # 解析错误
        "https://fast.example/missing",     # 永久错误 -> 死信
    ] + ["https://slow.example/post/%d" % i for i in range(6)]

    added = sum(sched.add(u, priority=1 if "post/1" in u else 0) for u in seeds)
    print("入队 %d / %d（其余被归一化去重）" % (added, len(seeds)))

    threads = [threading.Thread(target=worker, args=(sched, None)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("状态分布:", sched.stats())
    print("死信:", sched.dead_letters())
    print("解析样本:", sched.parse_samples())
    print("游标 fast.example:", sched.get_cursor("fast.example"))
    sched.close()

    # 模拟崩溃恢复：另开一批任务，取出一半不完成就"退出"
    print("\n--- 模拟重启 ---")
    s1 = Scheduler(db_path)
    for i in range(4):
        s1.add("https://fast.example/page/%d" % i)
    s1.next()
    s1.next()  # 两个任务停在 running，进程退出
    s1.close()

    s2 = Scheduler(db_path)
    print("重启后 pending=%d（running 已重置）" % s2.pending())
    while True:
        task = s2.next()
        if task is None:
            break
        s2.success(task.url, "ok")
    print("恢复后全部完成，stats:", s2.stats())
    s2.close()
    os.unlink(db_path)


if __name__ == "__main__":
    main()
