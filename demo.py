"""模拟重点站（shop）与长尾站（blog）的动态权重调度。"""

import random

from siteweight import Scheduler

SITES = ["shop", "news", "blog"]
PROFILE = {"shop": (0.95, 0.8), "news": (0.8, 0.4), "blog": (0.3, 0.1)}
ROUNDS = 60
WINDOW = 6


def main():
    random.seed(7)
    sched = Scheduler(SITES, guarantee_window=WINDOW)
    for _ in range(ROUNDS):
        site = sched.next()
        p_ok, p_updated = PROFILE[site]
        ok = random.random() < p_ok
        sched.feedback(site, {"ok": ok,
                              "updated": ok and random.random() < p_updated})

    print("== 权重变化 ==")
    for entry in sched.weight_log:
        print("tick=%02d %-4s %.3f -> %.3f"
              % (entry["tick"], entry["site"], entry["old"], entry["new"]))

    print("== 权重与等待 ==")
    stats = sched.stats()
    for site in SITES:
        st = stats[site]
        print("%-4s weight=%.3f picks=%2d avg_wait=%s max_wait=%s"
              % (site, st["weight"], st["picks"],
                 "%.1f" % st["avg_wait"], st["max_wait"]))

    assert stats["shop"]["weight"] > stats["blog"]["weight"]
    assert stats["blog"]["max_wait"] <= WINDOW
    assert stats["blog"]["picks"] >= ROUNDS // WINDOW - 1
    print("断言通过：重点站权重更高，长尾站有保底配额且等待有界")


if __name__ == "__main__":
    main()
