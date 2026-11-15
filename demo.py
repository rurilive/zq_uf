"""两个采集进程共享一份落盘预算的演示：占用、借还、回收、等待、对账。"""

import json
import multiprocessing
import os
import tempfile
import time


def _worker(d, name, rounds):
    os.environ["SITEBUDGET2_DIR"] = d
    from sitebudget2 import give_back, take
    for _ in range(rounds):
        r = take("shop", 3)
        print(f"[{name}] take -> {r}")
        if r["taken"]:
            time.sleep(0.05)
            print(f"[{name}] give_back -> {give_back('shop', 3)}")
        else:
            time.sleep(r["wait"] if r["wait"] > 0 else 0.3)


def _crasher(d):
    os.environ["SITEBUDGET2_DIR"] = d
    from sitebudget2 import take
    print("[crasher] take ->", take("shop", 4))
    os._exit(1)  # 模拟崩溃：不归还就退出


def main():
    d = tempfile.mkdtemp(prefix="sitebudget2-demo-")
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"budgets": {"shop": 8}, "lease_seconds": 1.0}, fh)
    os.environ["SITEBUDGET2_DIR"] = d
    from sitebudget2 import give_back, reclaim, stats, take

    print(take("shop", 5))
    print(take("shop", 3))
    print(give_back("shop", 2))

    ctx = multiprocessing.get_context("fork")
    workers = [ctx.Process(target=_worker, args=(d, f"collector-{i}", 3))
               for i in range(2)]
    for p in workers:
        p.start()
    for p in workers:
        p.join()

    crasher = ctx.Process(target=_crasher, args=(d,))
    crasher.start()
    crasher.join()
    print("崩溃后占用:", stats()["sites"]["shop"]["used"])
    time.sleep(1.2)  # 等租约到期
    print("到期回收:", reclaim())
    print("对账:", json.dumps(stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
