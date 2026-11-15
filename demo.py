"""dnscache2 演示：TTL 缓存、后台刷新、失败降权冷却、机房灰度切换。

用 FakeClock + ManualExecutor 让整个过程完全确定性，不依赖真实时间和线程。
"""

from dnscache2 import (
    Alerter,
    AllAddressesCoolingDownError,
    DnsCache,
    HealthConfig,
    ManualExecutor,
    ResolutionError,
    RolloutManager,
    StaticResolver,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class MutableResolver:
    """可热切换地址表、可模拟解析失败的后端。"""

    def __init__(self, table):
        self.table = table
        self.failing = False

    def resolve(self, host):
        if self.failing:
            raise ResolutionError("dns timeout")
        return StaticResolver(self.table).resolve(host)


def show(title, result):
    print(f"[{title}] ip={result['ip']} ttl={result['ttl']:.1f}s "
          f"health={result['health']} refreshed={result['refreshed']} "
          f"stale={result['stale']}")


def main():
    clock = FakeClock()
    alerter = Alerter()
    table = {"shop": [("10.0.0.1", "idc-a"), ("10.0.0.2", "idc-a")]}
    resolver = MutableResolver(table)
    cache = DnsCache(resolver, ttl=10.0, health_config=HealthConfig(),
                     executor=ManualExecutor(), clock=clock, alerter=alerter)

    # 1) 首次解析 + TTL 内复用（解析只发生一次）
    show("冷解析", cache.resolve("shop"))
    show("TTL内复用", cache.resolve("shop"))

    # 2) 过期后先返回旧地址，后台刷新完成再切换；同 IP 的健康度被继承
    cache.report("shop", "10.0.0.1", False, 0.9)  # 先制造一次降权
    clock.advance(11)
    show("过期当次仍给旧地址", cache.resolve("shop"))
    table["shop"] = [("10.0.0.1", "idc-a"), ("10.0.0.2", "idc-a"),
                     ("10.0.0.3", "idc-a")]  # 后端扩了一个地址
    cache.wait_for_refreshes()
    refreshed = cache.resolve("shop")
    show("后台刷新完成", refreshed)
    inherited = {c["ip"]: c["health"] for c in refreshed["candidates"]}
    print(f"[健康度继承] 10.0.0.1 刷新后健康度仍为 {inherited['10.0.0.1']}（未重置）")

    # 3) 连续失败降权冷却，冷却中的地址不再被选中；冷却到期后半开、慢慢恢复
    for _ in range(3):
        cache.report("shop", "10.0.0.1", False, 0.5)
    show("10.0.0.1冷却后选中", cache.resolve("shop"))
    clock.advance(2)  # 越过 1s 基础冷却
    rep = cache.report("shop", "10.0.0.1", True, 0.2)
    print(f"[半开放行] 10.0.0.1 冷却结束首次成功，健康度仅恢复到 {rep['health']}")
    for _ in range(10):
        cache.report("shop", "10.0.0.1", True, 0.2)
    show("逐步恢复后", cache.resolve("shop"))

    # 全部地址冷却时不返回坏地址，而是抛异常
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        for _ in range(3):
            cache.report("shop", ip, False, 0.5)
    try:
        cache.resolve("shop")
    except AllAddressesCoolingDownError as exc:
        print(f"[全部冷却] 拒绝选择冷却地址: {exc}")
    clock.advance(60)  # 冷却全部解除

    # 4) 刷新失败：保留旧结果并告警，不切换
    resolver.failing = True
    clock.advance(11)
    show("刷新失败仍返回旧地址", cache.resolve("shop"))
    cache.wait_for_refreshes()
    refresh_alerts = [e for e in alerter.events() if e["type"] == "refresh_failed"]
    print(f"[告警] {refresh_alerts[-1]}")
    resolver.failing = False

    # 5) 机房切换灰度：灰度站点先验证，确认健康后再扩大到同机房其余站点
    table.clear()
    table.update({
        "shop": [("10.0.0.1", "idc-a")],
        "cart": [("10.0.0.4", "idc-a")],
    })
    cache.invalidate(hosts=["shop"], sync=True)
    cache.resolve("cart")

    # 后端把 idc-a 的地址切到 idc-b
    table.clear()
    table.update({
        "shop": [("10.1.0.1", "idc-b")],
        "cart": [("10.1.0.2", "idc-b")],
    })
    rollout = RolloutManager(cache)
    print(f"[灰度开始] {rollout.begin_switch('idc-a', ['shop'])}")
    show("灰度站点已是新地址", cache.resolve("shop"))
    show("非灰度站点仍是旧地址", cache.resolve("cart"))
    print(f"[灰度确认] {rollout.confirm_switch('idc-a')}")
    show("全量切换后 shop", cache.resolve("shop"))
    show("全量切换后 cart", cache.resolve("cart"))


if __name__ == "__main__":
    main()
