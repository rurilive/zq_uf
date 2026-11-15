"""dnscache2 演示：TTL 缓存、健康度降权/冷却、机房灰度切换。"""

from dnscache2 import DnsCache, GrayVerificationError


class Clock:
    """可快进的假时钟。"""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class PendingSubmit:
    """捕获后台刷新任务，手动放行，用于展示刷新期间旧地址继续可用。"""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run_one(self):
        self.jobs.pop(0)()


def main():
    # 1. 兼容旧的模块级调用
    from dnscache2 import report, resolve
    print("1) 旧接口：", resolve("shop"))
    print("   上报：  ", report("shop", "10.0.0.1", True, 0.12))

    clock = Clock()
    table = {"shop": ["10.0.0.1", "10.0.0.2"]}

    def system_resolve(host):
        return list(table[host])

    alerts = []
    pending = PendingSubmit()
    cache = DnsCache(resolver=system_resolve, ttl=30, time_func=clock.now,
                     submit=pending, alerter=alerts.append)

    # 2. TTL 内复用；过期后台刷新，刷新期间旧地址继续可用，完成后才切换
    print("\n2) TTL 缓存与后台刷新")
    r1 = cache.resolve("shop")
    r2 = cache.resolve("shop")
    print("   首次：", r1)
    print("   复用：", r2, "（resolver 只调一次）")

    clock.advance(31)  # TTL 过期
    table["shop"] = ["10.0.0.3", "10.0.0.4"]  # 机房切走
    r3 = cache.resolve("shop")  # 触发后台刷新，但本次仍拿旧结果
    print("   过期当次仍返回旧地址：", r3["ip"], "refreshed =", r3["refreshed"],
          "待执行刷新任务 =", len(pending.jobs))
    pending.run_one()  # 后台刷新完成，地址表在此刻才整体替换
    r4 = cache.resolve("shop")
    print("   刷新完成后切换：      ", r4["ip"], "refreshed =", r4["refreshed"],
          "剩余 TTL =", r4["ttl"])

    # 3. 连续失败降权并冷却，冷却中不再被选中；成功后慢慢恢复
    print("\n3) 健康度降权与冷却")
    for _ in range(3):
        print("   失败上报：", cache.report("shop", "10.0.0.3", False, 0.5))
    print("   冷却后选中：", cache.resolve("shop")["ip"])
    print("   告警：", alerts[-1]["reason"], alerts[-1]["detail"])
    cache.report("shop", "10.0.0.4", True, 0.08)
    print("   健康度恢复：", cache.resolve("shop")["health"])

    # 4. 机房切换先灰度，验证不通过拒绝扩大；恢复后只刷新受影响站点
    print("\n4) 机房灰度切换")
    table["gray-a"] = ["10.1.0.1"]
    table["other-b"] = ["10.2.0.1"]
    cache.resolve("gray-a")
    cache.resolve("other-b")
    gen_before = cache._entries["other-b"].generation

    table["gray-a"] = ["10.1.0.9"]  # 灰度站点的新地址
    rollout = cache.begin_idc_switch(
        affected=["gray-a", "other-b"], gray=["gray-a"])
    print("   只刷新灰度站点，待执行任务 =", len(pending.jobs))
    pending.run_one()  # 灰度站点刷新完成
    ok, details = rollout.verify()
    print("   灰度验证：", ok, details, "待扩大：", rollout.status()["pending"])

    # 新地址连续失败，健康度不达标，禁止扩大
    for _ in range(3):
        cache.report("gray-a", "10.1.0.9", False, 0.5)
    try:
        rollout.expand()
    except GrayVerificationError as exc:
        print("   健康度不达标，拒绝扩大：", exc)

    # 灰度恢复后扩大；未受影响站点不会被刷新
    clock.advance(999)
    for _ in range(6):
        cache.report("gray-a", "10.1.0.9", True, 0.05)
    table["other-b"] = ["10.2.0.9"]
    rollout.expand()
    pending.run_one()  # 其余受影响站点刷新完成
    print("   扩大后 other-b：", cache.resolve("other-b")["ip"])
    print("   other-b 地址表版本变化：",
          cache._entries["other-b"].generation > gen_before)


if __name__ == "__main__":
    main()
