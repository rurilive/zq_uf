import json
import unittest

from rateadapt import acquire, feedback
from rateadapt import rate as mod


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Base(unittest.TestCase):
    def setUp(self):
        mod.reset()
        self.clock = FakeClock()
        mod.set_clock(self.clock, sleeper=self.clock.advance)

    def tearDown(self):
        mod.set_clock(None, None)
        mod.reset()

    def ok(self, site="shop", rtt=0.1):
        """申请并成功回填一次请求，必要时等到下一个令牌。"""
        res = acquire(site)
        if not res["ok"]:
            # 令牌桶按浮点推进，多等一点避免边界误差
            self.clock.advance(max(res["wait"], 1.0 / res["rate"]) + 1e-3)
            res = acquire(site)
        self.assertTrue(res["ok"], res)
        return feedback(site, 200, rtt)

    def rate_of(self, site="shop"):
        return mod.status(site)["rate"]


class BasicTest(Base):
    def test_default_start_rate(self):
        self.assertEqual(acquire("a")["rate"], 1.0)

    def test_acquire_reports_remaining_and_cooldown(self):
        res = acquire("a")
        self.assertTrue(res["ok"])
        for key in ("rate", "remaining", "remaining_tokens", "cooldown_until",
                    "cooldown_in", "concurrency_limit"):
            self.assertIn(key, res)
        self.assertIsNone(res["cooldown_until"])

    def test_burst_is_paced(self):
        # 令牌桶一次只放一个，连续突发不能绕过限速
        first = acquire("a")
        second = acquire("a")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "quota_exhausted")
        self.assertGreater(second["wait"], 0)

    def test_queue_waits_for_quota(self):
        self.assertTrue(acquire("a")["ok"])
        res = acquire("a", wait=True, max_wait=5.0)
        self.assertTrue(res["ok"])


class SpeedupTest(Base):
    def test_slow_speedup_after_success_streak(self):
        for _ in range(mod.SUCCESS_THRESHOLD - 1):
            self.ok()
        self.assertEqual(self.rate_of(), 1.0)
        res = self.ok()
        self.assertTrue(res["adjusted"])
        self.assertEqual(res["reason"], "success_streak")
        self.assertAlmostEqual(res["rate"], 1.1, places=3)

    def test_speedup_has_interval_and_cap(self):
        # 长时间成功最终停在 max_rate，不会越过上限
        for _ in range(500):
            self.clock.advance(1.0)
            self.ok()
        self.assertAlmostEqual(self.rate_of(), mod.MAX_RATE, places=3)
        res = self.ok()
        self.assertFalse(res["adjusted"])
        self.assertEqual(res["reason"], "max_rate")

        # 两次提速的间隔不小于 ADJUST_INTERVAL
        ups = [r["ts"] for r in mod.records("shop", hours=24, kind="speedup")]
        self.assertGreater(len(ups), 1)
        gaps = [b - a for a, b in zip(ups, ups[1:])]
        self.assertGreaterEqual(min(gaps), mod.ADJUST_INTERVAL - 1e-9)

    def test_sites_are_independent(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok("fast")
        self.assertAlmostEqual(self.rate_of("fast"), 1.1, places=3)
        self.assertEqual(self.rate_of("slow"), 1.0)


class SlowdownTest(Base):
    def test_429_halves_rate_and_cools_down(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok()
        before = self.rate_of()

        res = feedback("shop", 429, 1.2)
        self.assertTrue(res["adjusted"])
        self.assertEqual(res["reason"], "status_429")
        self.assertAlmostEqual(res["rate"], before * mod.SLOWDOWN_FACTOR, places=3)
        self.assertGreater(res["cooldown_in"], 0)
        self.assertIsNotNone(res["cooldown_until"])

        blocked = acquire("shop")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "cooldown")
        self.assertGreater(blocked["wait"], 0)

    def test_repeated_429_backs_off_exponentially(self):
        feedback("shop", 429)
        first = mod.status("shop")["cooldown_in"]
        self.clock.advance(first + 0.1)
        feedback("shop", 429)
        second = mod.status("shop")["cooldown_in"]
        self.assertGreater(second, first)
        self.assertLessEqual(second, mod.MAX_COOLDOWN)

    def test_429_during_cooldown_does_not_halve_again(self):
        feedback("shop", 429)
        rate = mod.status("shop")["rate"]
        # 冷却期内回来的 429（多是限流前发出的请求）不应把速率再折半
        res = feedback("shop", 429)
        self.assertFalse(res["adjusted"])
        self.assertEqual(res["reason"], "cooldown_active")
        self.assertAlmostEqual(mod.status("shop")["rate"], rate, places=3)

    def test_rate_has_floor(self):
        for _ in range(40):
            feedback("shop", 429)
            self.clock.advance(mod.MAX_COOLDOWN + 1)
        self.assertGreaterEqual(self.rate_of(), mod.MIN_RATE)

    def test_other_errors_do_not_speed_down_or_up(self):
        self.ok()
        res = feedback("shop", 500)
        self.assertFalse(res["adjusted"])
        self.assertEqual(res["reason"], "error_hold")
        self.assertEqual(res["rate"], 1.0)

    def test_recovers_from_low_rate_not_previous_rate(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.clock.advance(1.0)
            self.ok()
        peak = self.rate_of()
        dropped = feedback("shop", 429)["rate"]

        # 冷却结束后从低速重新开始
        self.clock.advance(mod.MAX_COOLDOWN + 1)
        self.assertTrue(acquire("shop")["ok"])
        self.assertAlmostEqual(mod.status("shop")["rate"], dropped, places=3)

        # 恢复期需要更长的成功连击，且不会一步跳回峰值
        after = self.ok()
        self.assertAlmostEqual(after["rate"], dropped, places=3)
        self.assertLess(mod.status("shop")["rate"], peak)


class BudgetTest(Base):
    def test_concurrency_and_rate_share_budget(self):
        state = mod.status("shop")
        self.assertEqual(
            state["concurrency_limit"],
            max(1, min(mod.MAX_CONCURRENCY, int(mod.BUDGET / state["rate"]))),
        )

    def test_slowdown_never_increases_concurrency(self):
        state = mod.status("shop")
        limit_before = state["concurrency_limit"]
        feedback("shop", 429)
        after = mod.status("shop")
        self.assertLessEqual(after["concurrency_limit"], limit_before)
        self.assertLess(after["rate"], state["rate"])

    def test_concurrency_caps_inflight(self):
        site = "shop"
        limit = mod.status(site)["concurrency_limit"]
        # 请求一直不回填（rtt 很大），令牌仍会累积，并发上限必须拦住
        for _ in range(limit):
            self.clock.advance(5.0)
            self.assertTrue(acquire(site)["ok"])
        self.clock.advance(5.0)
        denied = acquire(site)
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["reason"], "concurrency_full")
        self.assertEqual(denied["remaining"], 0)
        self.assertEqual(denied["inflight"], limit)


class RecordTest(Base):
    def test_records_include_speedup_and_slowdown(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok()
        feedback("shop", 429)
        kinds = [r["kind"] for r in mod.records("shop")]
        self.assertIn("speedup", kinds)
        self.assertIn("slowdown", kinds)

    def test_recent_hour_default_and_records_per_site(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok("shop")
        self.assertTrue(mod.records("shop"))
        self.assertEqual(mod.records("other"), [])

        self.clock.advance(3600 + 1)
        self.assertEqual(mod.records("shop"), [])
        # 一天内的记录仍可导出
        self.assertTrue(mod.records("shop", hours=24))

    def test_retention_is_one_day(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok()
        self.clock.advance(24 * 3600 + 1)
        self.assertTrue(acquire("shop"))  # 触发一次清理
        mod.records("shop", hours=48)
        self.assertEqual(mod.status("shop")["records_total"], 0)

    def test_cooldown_end_is_recorded(self):
        feedback("shop", 429)
        self.clock.advance(mod.MAX_COOLDOWN + 1)
        acquire("shop")
        kinds = [r["kind"] for r in mod.records("shop")]
        self.assertIn("recovered", kinds)

    def test_denials_are_viewable_per_site(self):
        acquire("shop")
        acquire("shop")  # quota_exhausted 被记录
        denied = mod.records("shop", kind="denied")
        self.assertTrue(denied)
        self.assertEqual(denied[-1]["kind"], "denied")
        self.assertTrue(mod.status("shop")["denied_recent"])
        self.assertFalse(mod.status("other")["denied_recent"])

    def test_export_json_and_csv(self):
        for _ in range(mod.SUCCESS_THRESHOLD):
            self.ok()
        feedback("shop", 429)

        payload = json.loads(mod.export_records("shop"))
        self.assertEqual(payload["site"], "shop")
        self.assertEqual(payload["count"], len(payload["records"]))
        self.assertGreaterEqual(payload["count"], 2)

        text = mod.export_records("shop", fmt="csv")
        lines = text.strip().splitlines()
        self.assertIn("kind", lines[0])
        self.assertEqual(len(lines), payload["count"] + 1)

        with self.assertRaises(ValueError):
            mod.export_records("shop", fmt="xml")

    def test_export_all_includes_denials(self):
        acquire("shop")
        acquire("shop")  # 额度不足，留档
        payload = json.loads(mod.export_records("shop", kind="all"))
        kinds = {r["kind"] for r in payload["records"]}
        self.assertIn("denied", kinds)
        csv_text = mod.export_records("shop", fmt="csv", kind="all")
        self.assertIn("denied", csv_text)

    def test_denial_records_are_throttled(self):
        acquire("shop")
        for _ in range(50):
            acquire("shop")
        self.assertEqual(len(mod.records("shop", kind="denied")), 1)
        self.clock.advance(mod.DENIAL_LOG_INTERVAL + 1)
        self.assertTrue(acquire("shop")["ok"])  # 令牌已补满，先消耗掉
        acquire("shop")                          # 再次额度不足，允许留档
        self.assertEqual(len(mod.records("shop", kind="denied")), 2)

    def test_release_returns_slot(self):
        acquire("shop")
        self.assertEqual(mod.status("shop")["inflight"], 1)
        mod.release("shop")
        self.assertEqual(mod.status("shop")["inflight"], 0)


if __name__ == "__main__":
    unittest.main()
