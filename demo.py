"""演示按站点自适应限速：RTT 抖动、429/5xx 降速与稳定后的缓慢恢复。"""

import random

from hostshield import acquire, release, shield

# 演示使用独立状态文件并从干净状态开始，避免污染默认共享状态
shield.reset()
random.seed(7)

SITES = ("shop", "news")


def simulate(rounds=18):
    # shop 前期健康、中途被限流、随后恢复；news 全程稳定但 RTT 会抖动
    for i in range(rounds):
        for site in SITES:
            permit = acquire(site)
            if not permit["granted"]:
                print("%2d %-4s 并发/令牌不足，建议等待 %.2fs"
                      % (i + 1, site, permit["wait"]))
                continue

            rtt = max(0.05, random.gauss(0.2 if site == "news" else 0.15, 0.06))
            if site == "shop" and 6 <= i <= 8:
                status = 429                       # 连续被限流 → 降速
                rtt = 0.9
            elif site == "shop" and i in (10, 12):
                status = 503                       # 间歇性 5xx
            else:
                status = 200
            info = release(site, round(rtt, 3), status)
            print("%2d %-4s rtt=%5.3f status=%d -> rate=%5.3f/s "
                  "concurrency=%d in_flight=%d | %s"
                  % (i + 1, site, rtt, status, info["rate"],
                     info["concurrency"], info["in_flight"], info["reason"]))


if __name__ == "__main__":
    simulate()
    print("\n最终各站状态：")
    for site, snap in shield.status().items():
        print("- %s: rate=%.3f/s concurrency=%d rtt_ema=%s success_rate=%.0f%%"
              % (site, snap["rate"], snap["concurrency"],
                 snap["rtt_ema"], snap["success_rate"] * 100))
        for reason in snap["reasons"]:
            print("    ·", reason)
