"""演示：缓存复用、后台刷新、故障降权、机房灰度切换。"""

from dnscache2 import DNSCache

# 注入时钟和后台执行器，让整个过程可复现（实际使用默认值即可）
now = [0.0]
jobs = []


def run_async(job):
    jobs.append(job)


cache = DNSCache(
    upstream={
        "shop": {"dc1": ["10.0.0.1", "10.0.0.2"], "dc2": ["10.0.0.11", "10.0.0.12"]},
        "blog": {"dc1": ["10.0.1.1"], "dc2": ["10.0.1.11"]},
    },
    ttl=30,
    clock=lambda: now[0],
    datacenter="dc1",
    canary_hosts=["shop"],
    run_async=run_async,
)

print("首次解析:", cache.resolve("shop"))
now[0] = 10
print("TTL 内复用:", cache.resolve("shop"))

# 过期：先返回旧地址，刷新在后台进行
now[0] = 40
print("过期仍可用（后台刷新中）:", cache.resolve("shop"))
print("待刷新任务:", len(jobs))
jobs.pop()()
print("后台刷新完成后（命中新结果）:", cache.resolve("shop"))

# 10.0.0.1 连续失败：降权并冷却，选路切到 10.0.0.2
for _ in range(2):
    print("上报失败:", cache.report("shop", "10.0.0.1", False, 5.0))
print("故障后选路:", cache.resolve("shop"))
print("可用程度排序:", cache.rank("shop"))

# 冷却结束后健康分慢慢恢复
now[0] = 400
print("冷却结束:", cache.resolve("shop"))
print("恢复中排序:", cache.rank("shop"))

# 机房切换：灰度站点验证通过后扩大范围
print("切换前:", cache.resolve("blog"))
result = cache.switch_datacenter(
    "dc2", canary=["shop"], probe=lambda host, ip, dc: True)
print("切换结果:", result)
print("切换后 shop:", cache.resolve("shop"))
print("切换后 blog:", cache.resolve("blog"))
print("告警:", cache.alerts)
