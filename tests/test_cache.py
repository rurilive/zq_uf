"""dnscache2 核心行为测试：缓存/后台刷新/健康度/定向失效/灰度切换。"""

import unittest

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
from dnscache2.resolver import FailingResolver


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class SwappableResolver:
    """可热切换解析表的解析器，模拟机房切换。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def resolve(self, host):
        self.calls.append(host)
        if host not in self.table:
            return StaticResolver().resolve(host)
        return StaticResolver(self.table).resolve(host)


def make_cache(resolver, ttl=10.0, health=None, clock=None, alerter=None):
    clock = clock or FakeClock()
    return clock, DnsCache(
        resolver, ttl=ttl, health_config=health or HealthConfig(),
        executor=ManualExecutor(), clock=clock,
        alerter=alerter or Alerter(),
    )


class CacheTest(unittest.TestCase):
    def test_ttl内复用结果不重复解析(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a"), ("10.0.0.2", "a")]})
        clock, cache = make_cache(resolver)

        first = cache.resolve("shop")
        second = cache.resolve("shop")

        self.assertTrue(first["refreshed"])
        self.assertFalse(second["refreshed"])
        self.assertEqual(resolver.calls, ["shop"])  # TTL 内只解析一次
        self.assertAlmostEqual(second["ttl"], 10.0, delta=0.01)
        self.assertEqual(len(second["candidates"]), 2)

    def test_过期后返回旧地址并后台刷新完成后切换(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a")]})
        clock, cache = make_cache(resolver)
        cache.resolve("shop")

        clock.advance(11)  # TTL 过期
        stale = cache.resolve("shop")
        # 刷新期间旧地址仍然可用
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["ip"], "10.0.0.1")
        self.assertFalse(stale["refreshed"])
        self.assertEqual(resolver.calls, ["shop"])  # 后台刷新尚未执行

        resolver.table = {"shop": [("10.0.0.9", "b")]}
        cache.wait_for_refreshes()  # 后台刷新完成
        fresh = cache.resolve("shop")
        self.assertEqual(fresh["ip"], "10.0.0.9")
        self.assertFalse(fresh["stale"])
        self.assertEqual(resolver.calls, ["shop", "shop"])

    def test_刷新失败保留旧结果并告警(self):
        failing = FailingResolver("dns timeout")
        clock, alerter = FakeClock(), Alerter()
        cache = DnsCache(failing, ttl=5.0, executor=ManualExecutor(),
                         clock=clock, alerter=alerter)
        # 先造一份旧缓存
        good = StaticResolver({"shop": [("10.0.0.1", "a")]})
        cache._resolver = good
        cache.resolve("shop")
        cache._resolver = failing

        clock.advance(6)
        result = cache.resolve("shop")  # 过期触发后台刷新
        cache.wait_for_refreshes()

        self.assertEqual(result["ip"], "10.0.0.1")  # 旧地址保留
        snap = cache.snapshot()["shop"]
        self.assertFalse(snap["last_refresh_ok"])
        self.assertTrue(any(e["type"] == "refresh_failed" for e in alerter.events()))

        # 冷路径无旧结果时解析失败直接抛异常并告警
        with self.assertRaises(ResolutionError):
            cache.resolve("brand-new-host")
        self.assertTrue(
            any(e["type"] == "cold_resolve_failed" for e in alerter.events())
        )

    def test_连续失败降权冷却且冷却中不被选中(self):
        resolver = SwappableResolver({
            "shop": [("10.0.0.1", "a"), ("10.0.0.2", "a")],
        })
        clock, cache = make_cache(resolver)
        cache.resolve("shop")

        # 10.0.0.1 连续失败 3 次进入冷却并降权
        for _ in range(3):
            rep = cache.report("shop", "10.0.0.1", False, 0.5)
        self.assertTrue(rep["cooling"])
        self.assertLess(rep["health"], 1.0)

        result = cache.resolve("shop")
        self.assertEqual(result["ip"], "10.0.0.2")  # 坏地址不再被选中
        self.assertTrue(result["candidates"][0]["ip"] == "10.0.0.2")
        cooling = {c["ip"]: c["cooling"] for c in result["candidates"]}
        self.assertTrue(cooling["10.0.0.1"])

    def test_全部冷却时不选冷却地址而抛异常(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a")]})
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        for _ in range(3):
            cache.report("shop", "10.0.0.1", False, 0.5)
        with self.assertRaises(AllAddressesCoolingDownError):
            cache.resolve("shop")

    def test_冷却恢复后半开放行并慢慢恢复健康度(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a")]})
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        for _ in range(3):
            cache.report("shop", "10.0.0.1", False, 0.5)
        with self.assertRaises(AllAddressesCoolingDownError):
            cache.resolve("shop")

        clock.advance(2)  # 超过 1s 基础冷却
        rep0 = cache.report("shop", "10.0.0.1", True, 0.2)
        self.assertFalse(rep0["cooling"])
        self.assertLess(rep0["health"], 1.0)  # 半开低分，不是立刻满血

        result = cache.resolve("shop")  # 重新进入候选
        self.assertEqual(result["ip"], "10.0.0.1")
        # 连续成功后健康度逐步恢复
        for _ in range(10):
            cache.report("shop", "10.0.0.1", True, 0.2)
        self.assertEqual(cache.snapshot()["shop"]["addresses"][0]["health"], 1.0)

    def test_健康度高的地址优先被选中(self):
        resolver = SwappableResolver({
            "shop": [("10.0.0.1", "a"), ("10.0.0.2", "a")],
        })
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        cache.report("shop", "10.0.0.1", False, 0.9)  # 一次失败降权但未冷却
        result = cache.resolve("shop")
        self.assertEqual(result["ip"], "10.0.0.2")

    def test_机房变化只刷新受影响站点(self):
        resolver = SwappableResolver({
            "shop": [("10.0.0.1", "idc-a")],
            "pay": [("10.0.1.1", "idc-b")],
        })
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        cache.resolve("pay")
        calls_before = len(resolver.calls)

        affected = cache.invalidate(idc="idc-a")  # 机房 a 切走
        self.assertEqual(affected, ["shop"])
        cache.wait_for_refreshes()
        # 只有 shop 被重新解析，pay 完全不动
        self.assertEqual(resolver.calls[calls_before:], ["shop"])
        self.assertEqual(cache.snapshot()["pay"]["generation"], 1)

        # 按站点名定向失效同样只影响指定站点
        cache.invalidate(hosts=["pay"], sync=True)
        self.assertEqual(cache.snapshot()["pay"]["generation"], 2)
        self.assertEqual(cache.snapshot()["shop"]["generation"], 2)

    def test_灰度切换先验证再扩大范围(self):
        resolver = SwappableResolver({
            "shop": [("10.0.0.1", "idc-a")],
            "cart": [("10.0.0.2", "idc-a")],
        })
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        cache.resolve("cart")
        rollout = RolloutManager(cache)

        # 后端切到机房 b 的新地址；先灰度 shop
        resolver.table = {
            "shop": [("10.1.0.1", "idc-b")],
            "cart": [("10.1.0.2", "idc-b")],
        }
        begin = rollout.begin_switch("idc-a", ["shop"])
        self.assertEqual(begin["canary_refreshed"], ["shop"])
        self.assertEqual(cache.resolve("shop")["ip"], "10.1.0.1")
        # 非灰度站点仍是旧地址
        self.assertEqual(cache.resolve("cart")["ip"], "10.0.0.2")

        confirmed = rollout.confirm_switch("idc-a")
        self.assertEqual(confirmed["stage"], "confirmed")
        self.assertEqual(confirmed["promoted_hosts"], ["cart"])
        self.assertEqual(cache.resolve("cart")["ip"], "10.1.0.2")
        self.assertEqual(rollout.switch_status("idc-a")["stage"], "confirmed")

    def test_灰度验证失败则阻断扩大并保留旧地址(self):
        resolver = SwappableResolver({
            "shop": [("10.0.0.1", "idc-a")],
            "cart": [("10.0.0.2", "idc-a")],
        })
        alerter = Alerter()
        clock, cache = make_cache(resolver, alerter=alerter)
        cache.resolve("shop")
        cache.resolve("cart")
        rollout = RolloutManager(cache)

        resolver.table = {
            "shop": [("10.1.0.1", "idc-b")],
            "cart": [("10.1.0.2", "idc-b")],
        }
        rollout.begin_switch("idc-a", ["shop"])
        # 新地址在灰度期连续失败进入冷却 -> 验证不通过
        for _ in range(3):
            cache.report("shop", "10.1.0.1", False, 0.5)
        verdict = rollout.confirm_switch("idc-a")
        self.assertFalse(verdict["confirmed"])
        self.assertEqual(verdict["failed_host"], "shop")
        # 扩大范围被阻断，非灰度站点保留旧地址
        self.assertEqual(cache.resolve("cart")["ip"], "10.0.0.2")
        self.assertTrue(
            any(e["type"] == "canary_switch_blocked" for e in alerter.events())
        )

    def test_刷新后的新地址保留旧地址健康度(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a"), ("10.0.0.2", "a")]})
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        cache.report("shop", "10.0.0.1", False, 0.9)  # 降权
        damaged = cache.snapshot()["shop"]["addresses"]
        self.assertLess(damaged[0]["health"], 1.0)

        cache.invalidate(hosts=["shop"], sync=True)  # 刷新，地址集合不变
        healed = cache.snapshot()["shop"]["addresses"]
        by_ip = {a["ip"]: a["health"] for a in healed}
        self.assertLess(by_ip["10.0.0.1"], 1.0)  # 健康度被继承，没有重置
        self.assertEqual(by_ip["10.0.0.2"], 1.0)

    def test_同一站点并发刷新只发一次(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a")]})
        clock, cache = make_cache(resolver)
        cache.resolve("shop")
        clock.advance(11)
        cache.resolve("shop")
        cache.resolve("shop")  # 连续过期请求
        cache.resolve("shop")
        self.assertEqual(cache._executor.pending(), 1)  # 只排队一个刷新任务

    def test_上报已被刷新替换的地址不产生告警(self):
        resolver = SwappableResolver({"shop": [("10.0.0.1", "a")]})
        clock, alerter = FakeClock(), Alerter()
        cache = DnsCache(resolver, ttl=5.0, executor=ManualExecutor(),
                         clock=clock, alerter=alerter)
        cache.resolve("shop")
        resolver.table = {"shop": [("10.9.9.9", "b")]}
        cache.invalidate(hosts=["shop"], sync=True)

        rep = cache.report("shop", "10.0.0.1", False, 0.5)  # 旧地址已不在候选
        self.assertFalse(rep["known"])
        self.assertEqual(alerter.events(), [])  # 正常替换不刷告警


if __name__ == "__main__":
    unittest.main()
