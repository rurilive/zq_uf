import unittest

from dnscache2 import (
    DnsCache,
    DnsResolveError,
    GrayVerificationError,
    NoAvailableAddress,
    report,
    resolve,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ManualSubmit:
    """捕获后台刷新任务，由测试手动驱动，保证时序确定。"""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run_one(self):
        self.jobs.pop(0)()

    @property
    def pending(self):
        return len(self.jobs)


class FakeResolver:
    def __init__(self, table, fail=False):
        self.table = table
        self.fail = fail
        self.calls = 0

    def __call__(self, host):
        self.calls += 1
        if self.fail:
            raise RuntimeError("resolver down")
        return list(self.table[host])


def make_cache(resolver, ttl=10.0, clock=None, submit=None, **kw):
    clock = clock or FakeClock()
    submit = submit if submit is not None else _inline
    return DnsCache(resolver=resolver, ttl=ttl, time_func=clock.now,
                    submit=submit, **kw), clock, submit


def _inline(fn):
    fn()


class ModuleApiTest(unittest.TestCase):
    """原有三个用例：模块级 resolve/report 的基本契约保持不变。"""

    def test_resolve_is_not_cached(self):
        self.assertFalse(resolve("shop")["cached"])

    def test_resolve_returns_address(self):
        self.assertEqual(resolve("shop")["ip"], "10.0.0.1")

    def test_report_does_not_refresh(self):
        self.assertFalse(report("shop", "10.0.0.1", True, 0.1)["refreshed"])


class TtlCacheTest(unittest.TestCase):
    def test_ttl内复用不重复解析(self):
        resolver = FakeResolver({"a": ["1.1.1.1"]})
        cache, clock, _ = make_cache(resolver, ttl=10)
        first = cache.resolve("a")
        second = cache.resolve("a")

        self.assertTrue(first["refreshed"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertFalse(second["refreshed"])
        self.assertEqual(resolver.calls, 1)
        self.assertGreater(second["ttl"], 0)

    def test_过期后台刷新_刷新期间旧地址继续可用(self):
        clock = FakeClock()
        manual = ManualSubmit()
        resolver = FakeResolver({"a": ["1.1.1.1"]})
        cache, _, _ = make_cache(resolver, ttl=10, clock=clock, submit=manual)

        cache.resolve("a")
        clock.advance(11)
        stale = cache.resolve("a")  # 触发后台刷新，但本次仍返回旧地址

        self.assertEqual(stale["ip"], "1.1.1.1")
        self.assertFalse(stale["refreshed"])
        self.assertEqual(manual.pending, 1)

        resolver.table["a"] = ["2.2.2.2"]
        manual.run_one()  # 刷新完成后才切换

        switched = cache.resolve("a")
        self.assertTrue(switched["refreshed"])
        self.assertEqual(switched["ip"], "2.2.2.2")
        self.assertFalse(cache.resolve("a")["refreshed"])  # 一次性通知
        self.assertEqual(resolver.calls, 2)

    def test_刷新单飞_重复请求不产生并发刷新(self):
        clock = FakeClock()
        manual = ManualSubmit()
        resolver = FakeResolver({"a": ["1.1.1.1"]})
        cache, _, _ = make_cache(resolver, ttl=10, clock=clock, submit=manual)

        cache.resolve("a")
        clock.advance(11)
        cache.resolve("a")
        cache.resolve("a")
        cache.resolve("a")
        self.assertEqual(manual.pending, 1)

    def test_刷新成功原子替换地址表_下线地址移除(self):
        clock = FakeClock()
        manual = ManualSubmit()
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=10, clock=clock, submit=manual)

        cache.resolve("h")
        resolver.table["h"] = ["a3"]
        clock.advance(11)
        cache.resolve("h")
        manual.run_one()
        cache.resolve("h")

        self.assertEqual(set(cache._entries["h"].addresses), {"a3"})
        self.assertEqual(cache.resolve("h")["ip"], "a3")


class RefreshFailureTest(unittest.TestCase):
    def test_刷新失败保留旧结果并告警_重试窗口后恢复(self):
        clock = FakeClock()
        manual = ManualSubmit()
        resolver = FakeResolver({"a": ["1.1.1.1"]})
        alerts = []
        cache, _, _ = make_cache(resolver, ttl=10, clock=clock,
                                 submit=manual, retry_interval=5,
                                 alerter=alerts.append)

        cache.resolve("a")
        clock.advance(11)
        resolver.fail = True
        cache.resolve("a")
        manual.run_one()  # 刷新失败

        self.assertEqual(alerts[-1]["reason"], "refresh_failed")
        self.assertEqual(cache.resolve("a")["ip"], "1.1.1.1")  # 旧结果保留
        self.assertEqual(manual.pending, 0)  # 重试窗口内不重复刷新

        clock.advance(6)  # 超过重试间隔
        resolver.fail = False
        resolver.table["a"] = ["2.2.2.2"]
        cache.resolve("a")
        manual.run_one()
        recovered = cache.resolve("a")
        self.assertEqual(recovered["ip"], "2.2.2.2")
        self.assertTrue(recovered["refreshed"])

    def test_首次加载失败抛出异常_无旧结果可保留(self):
        resolver = FakeResolver({"a": ["1.1.1.1"]}, fail=True)
        cache, _, _ = make_cache(resolver)
        with self.assertRaises(DnsResolveError):
            cache.resolve("a")


class HealthTest(unittest.TestCase):
    def test_失败降权_低健康度地址排到后面(self):
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=1000)
        self.assertEqual(cache.resolve("h")["ip"], "a1")  # 初始同分按 ip

        cache.report("h", "a1", False, 0.9)  # a1 健康度 0.8
        cache.report("h", "a2", True, 0.2)   # a2 健康度 1.0
        self.assertEqual(cache.resolve("h")["ip"], "a2")

    def test_同健康度按_rtt_升序(self):
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=1000)
        cache.resolve("h")
        cache.report("h", "a1", True, 0.9)
        cache.report("h", "a2", True, 0.2)
        self.assertEqual(cache.resolve("h")["ip"], "a2")

    def test_连续失败进入冷却_冷却中不被选中(self):
        resolver = FakeResolver({"h": ["a1", "a2"]})
        alerts = []
        cache, _, _ = make_cache(resolver, ttl=1000, alerter=alerts.append)
        cache.resolve("h")

        for _ in range(3):
            cache.report("h", "a1", False, 0.1)

        state = cache._entries["h"].addresses["a1"]
        self.assertGreater(state.cooldown_until, 0)
        self.assertEqual(cache.resolve("h")["ip"], "a2")
        self.assertEqual(alerts[-1]["reason"], "address_cooldown")

    def test_冷却时长指数退避_封顶(self):
        clock = FakeClock()
        resolver = FakeResolver({"h": ["x"]})
        cache, _, _ = make_cache(resolver, ttl=100000, clock=clock,
                                 fail_threshold=3, cooldown_base=1,
                                 cooldown_max=10)
        cache.resolve("h")
        state = cache._entries["h"].addresses["x"]

        for _ in range(3):
            cache.report("h", "x", False, 0.1)
        self.assertEqual(state.cooldown_until, 1.0)       # 第 1 轮 1s（时钟 0）

        clock.advance(2)                                   # 时钟 2
        for _ in range(3):
            cache.report("h", "x", False, 0.1)
        self.assertEqual(state.cooldown_until, 4.0)       # 第 2 轮 2s

        clock.advance(5)                                   # 时钟 7
        for _ in range(3):
            cache.report("h", "x", False, 0.1)
        self.assertEqual(state.cooldown_until, 11.0)      # 第 3 轮 4s

        clock.advance(10)                                  # 时钟 17
        for _ in range(3):
            cache.report("h", "x", False, 0.1)
        self.assertEqual(state.cooldown_until, 25.0)      # 第 4 轮 8s

        clock.advance(10)                                  # 时钟 27
        for _ in range(3):
            cache.report("h", "x", False, 0.1)
        self.assertEqual(state.cooldown_until, 37.0)      # 第 5 轮封顶 10s

    def test_冷却结束低健康度回候选_成功后逐步恢复(self):
        clock = FakeClock()
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=1000, clock=clock)
        cache.resolve("h")

        for _ in range(3):
            cache.report("h", "a1", False, 0.1)  # a1 冷却，健康度 0.4
        self.assertEqual(cache.resolve("h")["ip"], "a2")

        clock.advance(999)  # a1 冷却结束
        self.assertEqual(cache.resolve("h")["ip"], "a2")  # 健康度仍低，继续靠后

        for _ in range(3):
            cache.report("h", "a2", False, 0.1)  # a2 也冷却
        back = cache.resolve("h")
        self.assertEqual(back["ip"], "a1")
        self.assertAlmostEqual(back["health"], 0.4)

        cache.report("h", "a1", True, 0.1)
        self.assertAlmostEqual(cache.resolve("h")["health"], 0.5)
        cache.report("h", "a1", True, 0.1)
        self.assertAlmostEqual(cache.resolve("h")["health"], 0.6)

    def test_全部冷却时强制刷新_新地址接管(self):
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=1000)
        cache.resolve("h")
        for ip in ("a1", "a2"):
            for _ in range(3):
                cache.report("h", ip, False, 0.1)

        resolver.table["h"] = ["a3"]
        self.assertEqual(cache.resolve("h")["ip"], "a3")

    def test_全部冷却且无新地址_抛出异常(self):
        resolver = FakeResolver({"h": ["a1", "a2"]})
        cache, _, _ = make_cache(resolver, ttl=1000)
        cache.resolve("h")
        for ip in ("a1", "a2"):
            for _ in range(3):
                cache.report("h", ip, False, 0.1)
        with self.assertRaises(NoAvailableAddress):
            cache.resolve("h")

    def test_未知站点上报为无操作(self):
        cache, _, _ = make_cache(FakeResolver({}))
        result = cache.report("nope", "x", True, 0.1)
        self.assertEqual(result["health"], 1.0)
        self.assertFalse(result["refreshed"])


class IdcSwitchTest(unittest.TestCase):
    def _prime(self, hosts):
        clock = FakeClock()
        manual = ManualSubmit()
        table = {h: [h + "-old"] for h in hosts}
        resolver = FakeResolver(table)
        cache, _, _ = make_cache(resolver, ttl=1000, clock=clock,
                                 submit=manual)
        for host in hosts:
            cache.resolve(host)
        self.assertEqual(manual.pending, 0)  # 首次加载是同步的
        return cache, clock, manual, resolver, table

    def test_灰度只刷新灰度站点_验证通过后扩大(self):
        cache, clock, manual, resolver, table = self._prime(
            ["g1", "g2", "o1", "u1"])
        untouched_gen = cache._entries["u1"].generation

        rollout = cache.begin_idc_switch(
            affected=["g1", "g2", "o1"], gray=["g1"])
        self.assertEqual(manual.pending, 1)  # 只有灰度站点被刷新
        for host in ("g2", "o1", "u1"):
            self.assertFalse(cache._entries[host].refreshing)

        table["g1"] = ["g1-new"]
        manual.run_one()
        ok, details = rollout.verify()
        self.assertTrue(ok)
        self.assertEqual(details, {"g1": True})

        rollout.expand()
        self.assertEqual(manual.pending, 2)  # 扩大到其余受影响站点
        self.assertTrue(rollout.status()["expanded"])
        table["g2"] = ["g2-new"]
        table["o1"] = ["o1-new"]
        manual.run_one()
        manual.run_one()

        self.assertEqual(cache.resolve("g1")["ip"], "g1-new")
        self.assertEqual(cache.resolve("g2")["ip"], "g2-new")
        self.assertEqual(cache.resolve("o1")["ip"], "o1-new")
        self.assertEqual(cache.resolve("u1")["ip"], "u1-old")
        self.assertEqual(cache._entries["u1"].generation, untouched_gen)

    def test_灰度刷新失败_拒绝扩大且保留旧地址(self):
        cache, clock, manual, resolver, table = self._prime(["g1", "o1"])
        rollout = cache.begin_idc_switch(["g1", "o1"], ["g1"])

        resolver.fail = True
        manual.run_one()
        ok, details = rollout.verify()
        self.assertFalse(ok)
        self.assertEqual(details, {"g1": False})
        with self.assertRaises(GrayVerificationError):
            rollout.expand()
        self.assertEqual(rollout.status()["pending"], ["o1"])
        self.assertEqual(cache.resolve("g1")["ip"], "g1-old")

    def test_灰度健康度不达标_拒绝扩大(self):
        cache, clock, manual, resolver, table = self._prime(["g1", "o1"])
        rollout = cache.begin_idc_switch(["g1", "o1"], ["g1"])
        table["g1"] = ["g1-new"]
        manual.run_one()
        # 新地址连续失败，健康度降到阈值以下
        for _ in range(3):
            cache.report("g1", "g1-new", False, 0.1)
        clock.advance(999)  # 冷却结束，但健康度仍然很低
        ok, _ = rollout.verify()
        self.assertFalse(ok)
        with self.assertRaises(GrayVerificationError):
            rollout.expand()

    def test_灰度参数校验(self):
        cache, *_ = self._prime(["g1", "o1"])
        with self.assertRaises(ValueError):
            cache.begin_idc_switch(["g1"], [])
        with self.assertRaises(ValueError):
            cache.begin_idc_switch(["g1"], ["o1"])


if __name__ == "__main__":
    unittest.main()
