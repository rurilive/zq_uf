import math
import unittest

from covmatrix import report, scan
from covmatrix.scan import (
    COOLDOWN_SECONDS,
    DEFAULT_RATE,
    MAX_ADJUSTMENTS,
    MAX_CONSECUTIVE_THROTTLES,
    MIN_RATE,
)

T0 = 1_000_000


class ScanTest(unittest.TestCase):
    def test_initial_rate_defaults(self):
        result = scan({"host": "10.0.0.5"})
        self.assertEqual(result["rate"], DEFAULT_RATE)
        self.assertEqual(result["adjustments"], 0)
        self.assertIsNone(result["throttle"])
        self.assertEqual(result["resume"], 0)
        self.assertFalse(result["paused"])

    def test_throttle_hint_slowdown_and_keeps_raw_text(self):
        result = scan(
            {"host": "10.0.0.7", "throttle": "429 too many requests", "now": T0}
        )
        self.assertEqual(result["rate"], DEFAULT_RATE // 2)
        self.assertEqual(result["adjustments"], 1)
        # 限流原文保留，便于判断是目标侧限流还是网络问题
        self.assertEqual(result["throttle"], "429 too many requests")
        self.assertIn("429 too many requests", result["reason"])
        self.assertEqual(result["recover_in_seconds"], COOLDOWN_SECONDS)

    def test_latency_and_error_rate_also_slowdown(self):
        slow = scan({"host": "10.0.0.11", "latency_ms": 900, "now": T0})
        self.assertEqual(slow["rate"], DEFAULT_RATE // 2)
        self.assertEqual(slow["history"][0]["trigger"], "latency")

        slow2 = scan({"host": "10.0.0.12", "error_rate": 0.5, "now": T0})
        self.assertEqual(slow2["rate"], DEFAULT_RATE // 2)
        self.assertEqual(slow2["history"][0]["trigger"], "error_rate")

    def test_gradual_recovery_after_cooldown(self):
        host = "10.0.0.13"
        scan({"host": host, "throttle": "slow down", "now": T0})
        mid = scan({"host": host, "now": T0 + COOLDOWN_SECONDS})
        self.assertGreater(mid["rate"], DEFAULT_RATE // 2)
        self.assertEqual(mid["history"][-1]["trigger"], "recovery")
        # 冷却未结束时不提速
        still = scan({"host": host, "now": T0 + COOLDOWN_SECONDS + 1})
        self.assertEqual(still["rate"], mid["rate"])

    def test_resume_from_last_position(self):
        scan({"host": "10.0.0.14", "progress": 320, "now": T0})
        again = scan({"host": "10.0.0.14", "now": T0 + 10})
        self.assertEqual(again["resume"], 320)
        self.assertEqual(again["progress"], 320)

    def test_same_subnet_targets_independent(self):
        scan({"host": "10.0.0.20", "throttle": "429", "now": T0})
        other = scan({"host": "10.0.0.21", "now": T0})
        throttled = report("10.0.0.20")["targets"][0]
        self.assertEqual(other["rate"], DEFAULT_RATE)
        self.assertEqual(throttled["rate"], DEFAULT_RATE // 2)

    def test_consecutive_throttles_pause_round(self):
        host = "10.0.0.22"
        for i in range(MAX_CONSECUTIVE_THROTTLES):
            result = scan(
                {"host": host, "throttle": "429 again", "now": T0 + i * 10}
            )
        self.assertTrue(result["paused"])
        self.assertIn("本轮暂停", result["reason"])
        self.assertIsNotNone(result["recover_in_seconds"])
        self.assertGreater(result["recover_in_seconds"], 0)
        # 退避未结束：不扫描，状态保持
        waiting = scan({"host": host, "now": T0 + 30})
        self.assertTrue(waiting["paused"])
        # 退避结束自动恢复，并从 resume 位置继续
        scan({"host": host, "progress": 88, "now": T0 + 30})
        recovered = scan({"host": host, "now": T0 + 4000})
        self.assertFalse(recovered["paused"])
        self.assertEqual(recovered["resume"], 88)

    def test_adjustment_count_cap(self):
        host = "10.0.0.23"
        rate = DEFAULT_RATE
        # 降速与冷却后恢复交替，累积调整次数直到上限
        for i in range(MAX_ADJUSTMENTS // 2):
            throttled = scan(
                {"host": host, "throttle": "429 #%d" % i, "now": T0 + i * 200}
            )
            rate = max(MIN_RATE, rate // 2)
            self.assertEqual(throttled["rate"], rate)
            recovered = scan({"host": host, "now": T0 + i * 200 + COOLDOWN_SECONDS})
            rate = min(DEFAULT_RATE, math.ceil(rate * 1.25))
            self.assertEqual(recovered["rate"], rate)
        self.assertEqual(recovered["adjustments"], MAX_ADJUSTMENTS)
        capped = scan(
            {"host": host, "throttle": "429 once more",
             "now": T0 + 100000}
        )
        self.assertEqual(capped["adjustments"], MAX_ADJUSTMENTS)
        self.assertEqual(capped["rate"], rate)
        self.assertIn("上限", capped["reason"])

    def test_rate_floor(self):
        host = "10.0.0.24"
        result = None
        for i in range(10):
            result = scan(
                {"host": host, "throttle": "429 #%d" % i, "now": T0 + i * 100}
            )
        self.assertGreaterEqual(result["rate"], MIN_RATE)

    def test_report_aggregates_adjustments_with_before_after(self):
        scan({"host": "10.0.0.25", "throttle": "429 raw", "now": T0})
        scan({"host": "10.0.0.26", "latency_ms": 800, "now": T0})
        full = report()
        self.assertEqual(full["count"], len(full["targets"]))
        self.assertGreaterEqual(len(full["adjustments"]), 2)
        entry = full["adjustments"][0]
        self.assertEqual(entry["before"], DEFAULT_RATE)
        self.assertEqual(entry["after"], DEFAULT_RATE // 2)
        self.assertTrue(entry["reason"])
        self.assertIn("trigger", entry)
        one = report("10.0.0.25")
        self.assertEqual(one["count"], 1)
        self.assertTrue(all(a["target"] == "10.0.0.25" for a in one["adjustments"]))

    def test_invalid_target(self):
        with self.assertRaises(ValueError):
            scan("10.0.0.27")
        with self.assertRaises(ValueError):
            scan({})


if __name__ == "__main__":
    unittest.main()
