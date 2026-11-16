import importlib
import unittest

from covmatrix import report, scan

scan_module = importlib.import_module("covmatrix.scan")
from covmatrix.scan import (  # noqa: E402
    DEFAULT_RATE,
    MAX_ADJUSTMENTS,
    PAUSE_AFTER_THROTTLES,
    RECOVER_STEP,
)


class ScanTest(unittest.TestCase):
    def setUp(self):
        scan_module._scans.clear()

    def test_first_scan_uses_default_rate(self):
        result = scan({"host": "10.0.0.5"})
        self.assertEqual(result["rate"], DEFAULT_RATE)
        self.assertEqual(result["adjustments"], 0)

    def test_throttle_hint_slows_down_and_keeps_raw_text(self):
        scan({"host": "10.0.0.6"})
        result = scan({"host": "10.0.0.6", "throttle": "429 too many requests"})
        self.assertEqual(result["rate"], DEFAULT_RATE // 2)
        self.assertEqual(result["throttle"], "429 too many requests")
        self.assertEqual(result["adjustments"], 1)
        self.assertIn("限流", result["reason"])
        self.assertGreater(result["recover_eta"], 0)

    def test_resume_continues_from_last_position(self):
        first = scan({"host": "10.0.0.7"})
        second = scan({"host": "10.0.0.7"})
        self.assertGreater(first["resume"], 0)
        self.assertEqual(second["resume"], first["resume"] + second["rate"])

    def test_recovery_is_gradual_after_slowdown(self):
        scan({"host": "10.0.0.8"})
        slowed = scan({"host": "10.0.0.8", "throttle": "slow down"})
        recovered = scan({"host": "10.0.0.8"})
        self.assertEqual(recovered["rate"], slowed["rate"] + RECOVER_STEP)

    def test_recover_eta_zero_at_full_rate(self):
        result = scan({"host": "10.0.0.9"})
        self.assertEqual(result["recover_eta"], 0)

    def test_pause_after_consecutive_throttles(self):
        scan({"host": "10.0.0.10"})
        result = None
        for _ in range(PAUSE_AFTER_THROTTLES):
            result = scan({"host": "10.0.0.10", "throttle": "429"})
        self.assertTrue(result["paused"])
        self.assertIn("暂停本轮", result["reason"])
        resume_at_pause = result["resume"]
        still_paused = scan({"host": "10.0.0.10", "throttle": "429"})
        self.assertEqual(still_paused["resume"], resume_at_pause)

    def test_slow_latency_triggers_slowdown(self):
        scan({"host": "10.0.0.11"})
        result = scan({"host": "10.0.0.11", "latency_ms": 2500})
        self.assertLess(result["rate"], DEFAULT_RATE)
        self.assertIn("响应变慢", result["reason"])

    def test_error_rate_triggers_slowdown(self):
        scan({"host": "10.0.0.12"})
        result = scan({"host": "10.0.0.12", "error_rate": 0.4})
        self.assertLess(result["rate"], DEFAULT_RATE)
        self.assertIn("错误率", result["reason"])

    def test_adjustment_count_has_cap(self):
        scan({"host": "10.0.0.13"})
        result = None
        for _ in range(MAX_ADJUSTMENTS * 3):
            scan({"host": "10.0.0.13", "error_rate": 0.9})
            result = scan({"host": "10.0.0.13"})
        self.assertEqual(result["adjustments"], MAX_ADJUSTMENTS)
        self.assertIn("上限", result["reason"])

    def test_same_subnet_targets_adjust_independently(self):
        scan({"host": "10.0.0.20"})
        scan({"host": "10.0.0.21"})
        throttled = scan({"host": "10.0.0.20", "throttle": "429"})
        healthy = scan({"host": "10.0.0.21"})
        self.assertLess(throttled["rate"], healthy["rate"])

    def test_report_adjustments_keep_from_to_and_reason(self):
        scan({"host": "10.0.0.30"})
        scan({"host": "10.0.0.30", "throttle": "429 too many requests"})
        result = report("10.0.0.30")
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["adjustments"]), 1)
        entry = result["adjustments"][0]
        self.assertEqual(entry["from"], DEFAULT_RATE)
        self.assertEqual(entry["to"], DEFAULT_RATE // 2)
        self.assertIn("429 too many requests", entry["reason"])

    def test_report_all_targets(self):
        scan({"host": "10.0.0.40"})
        scan({"host": "10.0.0.41", "throttle": "429"})
        result = report()
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["adjustments"]), 1)
        rates = {row["target"]: row["rate"] for row in result["targets"]}
        self.assertEqual(rates["10.0.0.40"], DEFAULT_RATE)
        self.assertLess(rates["10.0.0.41"], DEFAULT_RATE)


if __name__ == "__main__":
    unittest.main()
