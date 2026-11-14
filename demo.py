"""演示：分域缓存、并发单飞、令牌过期重取、一次性消费、刷新失败回退。"""

import logging
import threading

import csrftoken

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# 假时钟 + 短 TTL，模拟令牌过期；fetcher 可切换成失败，模拟服务端故障。
now = [1000.0]
state = {"fail": False, "n": 0}


def fetcher(page, step):
    if state["fail"]:
        raise RuntimeError("服务端 503，令牌接口不可用")
    state["n"] += 1
    return "tok-%d" % state["n"]


csrftoken.configure(fetcher=fetcher, ttl=5.0, clock=lambda: now[0])

print("== 分域与缓存 ==")
print(csrftoken.get("login:password", 1))  # 抓取 tok-1
print(csrftoken.get("login:password", 1))  # 命中缓存，仍是 tok-1
print(csrftoken.get("login:password", 2))  # 另一个步骤，独立作用域 tok-2

print("== 并发单飞 ==")
barrier = threading.Barrier(6)
seen = []


def worker():
    barrier.wait()
    seen.append(csrftoken.get("login:password", 1)["token"])


threads = [threading.Thread(target=worker) for _ in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("6 个并发线程拿到:", sorted(set(seen)))  # 同域只抓一次，全部复用

print("== 令牌过期 ==")
now[0] += 10  # 越过 TTL，令牌过期
print(csrftoken.get("login:password", 1))  # 过期重取新令牌

print("== 一次性消费 ==")
print(csrftoken.consume("login:password", 1))  # True：放行一次
print(csrftoken.consume("login:password", 1))  # False：已消费

print("== 刷新失败回退 ==")
current = csrftoken.refresh("login:password", 1)["token"]
state["fail"] = True  # 模拟服务端故障
fell_back = csrftoken.refresh("login:password", 1)["token"]
print("刷新失败，回退令牌:", fell_back, "(== 上一枚: %s)" % (fell_back == current))

csrftoken.invalidate("login:password")
print("== 统计 ==")
print(csrftoken.stats())
