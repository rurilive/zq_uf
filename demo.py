"""两个站点的混合流量演示：日额度 + 突发额度 + 超额等待。"""

import time

from crawlbudget import configure, consume, remaining, reset, stats

MAX_WAIT = 2.0  # 可接受的最大等待秒数，超过则放弃本次请求


def fetch(site, n=1):
    """突发限流就睡眠重试；日额度耗尽（等待过久）就放弃。"""
    while True:
        result = consume(site, n)
        if isinstance(result, int):
            return result
        if result > MAX_WAIT:
            print("%s: 日额度耗尽，需等 %.0fs，放弃本次请求" % (site, result))
            return None
        print("%s: 突发限流，等待 %.2fs" % (site, result))
        time.sleep(result)


reset()
configure("shop", daily=6, burst=2, window=0.2)
configure("news", daily=3, burst=1, window=0.2)

print("== 突发额度：shop 连续 3 个请求（桶容量 2）==")
for _ in range(3):
    fetch("shop")

time.sleep(0.3)  # 等令牌桶回满，进入混合流量

print("== 混合流量：shop/news 交替 3 轮 ==")
for _ in range(3):
    fetch("shop")
    fetch("news")
    time.sleep(0.25)

print("== 日额度耗尽：shop 第 7 个、news 第 4 个请求 ==")
fetch("shop")
fetch("news")

report = stats()
for name, row in report["sites"].items():
    print(
        "%s: 消耗 %d 剩余 %d 拒绝 %d"
        % (name, row["spent"], row["remaining"], row["rejected"])
    )
g = report["global"]
print("global: 消耗 %d 剩余 %d 拒绝 %d" % (g["spent"], g["remaining"], g["rejected"]))

assert remaining("shop") == 0 and remaining("news") == 0
assert report["sites"]["shop"] == {"spent": 6, "remaining": 0, "rejected": 2}
assert report["sites"]["news"] == {"spent": 3, "remaining": 0, "rejected": 1}
assert report["global"]["spent"] == 9
print("demo 断言全部通过")
