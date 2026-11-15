import unittest

import dnscache2
from dnscache2 import DNSCache, report, resolve

UPSTREAM = {
    "shop": {"dc1": ["10.0.0.1", "10.0.0.2"], "dc2": ["10.0.0.11", "10.0.0.12"]},
    "blog": {"dc1": ["10.0.1.1"], "dc2": ["10.0.1.11"]},
}


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class CacheTest(unittest.TestCase):
    def setUp(self):
        dnscache2.clear()
        self.clock = Clock()
        self.jobs = []
        self.alerts = []
        self.cache = DNSCache(
            upstream=dict(UPSTREAM),
            ttl=30,
            clock=self.clock,
            datacenter="dc1",
            canary_hosts=["shop"],
            run_async=self.jobs.append,
            alert=self.alerts.append,
        )

    def flush(self):
        while self.jobs:
            self.jobs.pop(0)()

    # ---- TTL 复用与后台刷新 ----

    def test_second_resolve_reuses_cache(self):
        first = self.cache.resolve("shop")
        self.assertTrue(first["refreshed"])
        self.assertFalse(first["cached"])
        self.clock.now = 10
        second = self.cache.resolve("shop")
        self.assertTrue(second["cached"])
        self.assertFalse(second["refreshed"])
        self.assertEqual(second["ttl"], 20.0)
        self.assertEqual(self.jobs, [])

    def test_expired_result_is_still_usable_and_refreshes_in_background(self):
        self.cache.resolve("shop")
        self.clock.now = 40
        stale = self.cache.resolve("shop")
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["ip"], "10.0.0.1")
        self.assertEqual(len(self.jobs), 1)

        self.cache.upstream = {
            "shop": {"dc1": ["10.0.0.9"], "dc2": ["10.0.0.11"]}}
        self.flush()
        fresh = self.cache.resolve("shop")
        self.assertEqual(fresh["ip"], "10.0.0.9")
        self.assertFalse(fresh["refreshed"])  # 刷新发生在后台任务里
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["ttl"], 30.0)

    def test_expired_refresh_is_scheduled_only_once(self):
        self.cache.resolve("shop")
        self.clock.now = 40
        self.cache.resolve("shop")
        self.cache.resolve("shop")
        self.assertEqual(len(self.jobs), 1)

    def test_resolve_returns_selected_address_ttl_health_and_refresh_flag(self):
        result = self.cache.resolve("shop")
        for key in ("ip", "ttl", "health", "refreshed", "cached", "stale"):
            self.assertIn(key, result)
        self.assertEqual(result["ip"], "10.0.0.1")
        self.assertEqual(result["health"], 1.0)

    # ---- 失败降权与冷却 ----

    def test_consecutive_failure_demotes_and_cools_down(self):
        self.cache.resolve("shop")
        for _ in range(2):
            info = self.cache.report("shop", "10.0.0.1", False, 5.0)
        self.assertTrue(info["cooling"])
        self.assertLess(info["health"], 0.5)

        result = self.cache.resolve("shop")
        self.assertEqual(result["ip"], "10.0.0.2")
        self.assertEqual(result["addresses"][-1]["ip"], "10.0.0.1")
        self.assertTrue(result["addresses"][-1]["cooling"])

    def test_cooling_address_is_never_selected(self):
        self.cache.resolve("shop")
        self.cache.report("shop", "10.0.0.1", False, 5.0)
        self.cache.report("shop", "10.0.0.2", False, 5.0)
        result = self.cache.resolve("shop")
        self.assertIsNone(result["ip"])
        self.assertEqual(result["reason"], "all_cooldown")

    def test_address_recovers_after_cooldown(self):
        self.cache.resolve("shop")
        for _ in range(2):
            self.cache.report("shop", "10.0.0.1", False, 5.0)
        self.clock.now = 400
        ranked = self.cache.rank("shop")
        self.assertFalse(any(item["cooling"] for item in ranked))
        self.assertAlmostEqual(ranked[0]["health"], 1.0)
        self.assertLess(ranked[-1]["health"], 1.0)  # 恢复中但仍低于健康地址

    def test_success_recovers_health_and_tracks_rtt(self):
        self.cache.resolve("shop")
        self.cache.report("shop", "10.0.0.1", False, 5.0)
        ok = self.cache.report("shop", "10.0.0.1", True, 1.0)
        self.assertFalse(ok["cooling"])
        self.assertEqual(ok["fails"], 0)
        self.assertGreater(ok["health"], 0.5)

    def test_report_does_not_refresh(self):
        self.cache.resolve("shop")
        self.assertFalse(self.cache.report("shop", "10.0.0.1", True, 0.1)["refreshed"])

    # ---- 刷新失败 ----

    def test_refresh_failure_keeps_old_address_and_alerts(self):
        self.cache.resolve("shop")
        self.cache.upstream = {}
        self.clock.now = 40
        stale = self.cache.resolve("shop")
        self.assertEqual(stale["ip"], "10.0.0.1")
        self.flush()
        kept = self.cache.resolve("shop")
        self.assertEqual(kept["ip"], "10.0.0.1")
        self.assertTrue(kept["stale"])
        self.assertTrue(self.alerts)

    def test_refresh_failure_backs_off(self):
        self.cache.resolve("shop")
        self.cache.upstream = {}
        self.clock.now = 40
        self.cache.resolve("shop")
        self.flush()
        self.clock.now = 41
        self.cache.resolve("shop")
        self.assertEqual(self.jobs, [])  # 退避期内不再刷新
        self.clock.now = 100
        self.cache.resolve("shop")
        self.assertEqual(len(self.jobs), 1)

    def test_failed_refresh_does_not_override_newer_result(self):
        self.cache.resolve("shop")
        self.clock.now = 40
        self.cache.resolve("shop")  # 排入后台刷新
        self.cache.resolve("shop", force=True)  # 期间被同步刷新
        self.cache.upstream = {}
        self.flush()  # 过时的后台任务失败
        result = self.cache.resolve("shop")
        self.assertEqual(result["ip"], "10.0.0.1")
        self.assertFalse(result["stale"])
        self.assertIsNone(result["error"])

    # ---- 机房切换 ----

    def test_datacenter_change_refreshes_only_affected_hosts(self):
        self.cache.resolve("shop")
        self.cache.resolve("blog")
        self.cache.upstream = dict(UPSTREAM)
        result = self.cache.switch_datacenter("dc2", canary=["shop"])
        self.assertTrue(result["validated"])
        self.assertEqual(result["canary"], ["shop"])
        self.assertEqual(sorted(result["switched"]), ["blog", "shop"])
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.11")
        self.assertEqual(self.cache.resolve("blog")["ip"], "10.0.1.11")

    def test_canary_failure_keeps_everything_on_old_datacenter(self):
        self.cache.resolve("shop")
        self.cache.resolve("blog")
        result = self.cache.switch_datacenter(
            "dc2", canary=["shop"], probe=lambda host, ip, dc: False)
        self.assertFalse(result["validated"])
        self.assertEqual(result["switched"], [])
        self.assertIn("shop", result["failed"])
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.1")
        self.assertEqual(self.cache.resolve("blog")["ip"], "10.0.1.1")
        self.assertTrue(self.alerts)

    def test_probe_validation_allows_switch(self):
        self.cache.resolve("shop")
        seen = []

        def probe(host, ip, dc):
            seen.append((host, ip, dc))
            return ip.endswith("12")  # 只认 10.0.0.12

        result = self.cache.switch_datacenter("dc2", canary=["shop"], probe=probe)
        self.assertTrue(result["validated"])
        self.assertIn(("shop", "10.0.0.11", "dc2"), seen)
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.12")

    def test_missing_address_in_new_datacenter_keeps_old_entry(self):
        self.cache.upstream = {"shop": {"dc1": ["10.0.0.1"]}}
        self.cache.resolve("shop")
        self.cache.upstream = {"shop": {"dc1": ["10.0.0.1"], "dc2": []}}
        result = self.cache.switch_datacenter("dc2", canary=["shop"])
        self.assertFalse(result["validated"])
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.1")

    def test_failed_hosts_keep_old_while_others_switch(self):
        self.cache.resolve("shop")
        self.cache.resolve("blog")
        # blog 在新机房没有地址，shop 有
        self.cache.upstream = {"shop": {"dc1": ["10.0.0.1"],
                                        "dc2": ["10.0.0.11"]},
                               "blog": {"dc1": ["10.0.1.1"]}}
        result = self.cache.switch_datacenter("dc2", canary=["shop"])
        self.assertTrue(result["validated"])
        self.assertEqual(result["switched"], ["shop"])
        self.assertIn("blog", result["failed"])
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.11")
        # blog 继续使用旧地址，并会在下次 resolve 时重试
        self.assertEqual(self.cache.resolve("blog")["ip"], "10.0.1.1")
        self.assertTrue(self.alerts)

    def test_switch_without_affected_hosts_is_noop(self):
        self.cache.resolve("shop")
        self.cache.switch_datacenter("dc2")
        result = self.cache.switch_datacenter("dc2")
        self.assertTrue(result["validated"])
        self.assertEqual(result["switched"], [])
        self.assertEqual(self.cache.resolve("shop")["ip"], "10.0.0.11")

    def test_resolve_with_datacenter_switches_that_host_only(self):
        self.cache.resolve("shop")
        self.cache.resolve("blog")
        switched = self.cache.resolve("shop", datacenter="dc2")
        self.assertEqual(switched["ip"], "10.0.0.11")
        self.assertEqual(self.cache.resolve("blog")["ip"], "10.0.1.1")

    # ---- 默认入口 ----

    def test_default_resolve_returns_address(self):
        self.assertEqual(resolve("shop")["ip"], "10.0.0.1")

    def test_resolve_is_cached_by_default(self):
        self.assertFalse(resolve("shop")["cached"])
        self.assertTrue(resolve("shop")["cached"])

    def test_default_report_does_not_refresh(self):
        resolve("shop")
        self.assertFalse(report("shop", "10.0.0.1", True, 0.1)["refreshed"])

    def test_unknown_host_is_reported_as_unresolved(self):
        result = self.cache.resolve("unknown")
        self.assertIsNone(result["ip"])
        self.assertEqual(result["reason"], "unresolved")
        self.assertTrue(result["error"])


if __name__ == "__main__":
    unittest.main()
