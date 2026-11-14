import time
import unittest

from qualitygate import audit, check, waive
from qualitygate.gate import _reset


class GateTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def test_any_failure_blocks(self):
        result = check({"rules": [{"name": "a", "failed": False}, {"name": "b", "failed": True}]})
        self.assertTrue(result["blocked"])

    def test_no_severity(self):
        result = check({"rules": [{"name": "a", "failed": True}]})
        self.assertNotIn("levels", result)

    def test_waive_not_applied(self):
        self.assertFalse(waive("a", 100, "known")["active"])

    def test_info_and_warning_alone_do_not_block(self):
        result = check({
            "rules": [
                {"name": "hint", "failed": True, "level": "info", "failed_rows": 2},
                {"name": "warn", "failed": True, "level": "warning", "failed_rows": 5},
            ]
        })
        self.assertFalse(result["blocked"])
        self.assertEqual(result["counts"]["info"], 1)
        self.assertEqual(result["counts"]["warning"], 1)
        self.assertEqual(result["counts"]["block"], 0)

    def test_block_level_always_blocks(self):
        result = check({"rules": [{"name": "hard", "failed": True, "level": "block", "failed_rows": 1}]})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["failed"], ["hard"])

    def test_warning_blocks_by_failed_rows(self):
        small = check({"rules": [{"name": "w", "failed": True, "level": "warning", "failed_rows": 100}]})
        self.assertFalse(small["blocked"])
        big = check({"rules": [{"name": "w", "failed": True, "level": "warning", "failed_rows": 1000}]})
        self.assertTrue(big["blocked"])

    def test_warning_blocks_by_impact_ratio(self):
        result = check({
            "rules": [{
                "name": "w", "failed": True, "level": "warning",
                "failed_rows": 50, "total_rows": 1000,
            }]
        })
        self.assertTrue(result["blocked"])

    def test_warning_threshold_overridable_per_batch(self):
        batch = {
            "thresholds": {"warning_rows": 10, "warning_ratio": 0.5},
            "rules": [{"name": "w", "failed": True, "level": "warning", "failed_rows": 11}],
        }
        self.assertTrue(check(batch)["blocked"])

    def test_active_waiver_suppresses_failure(self):
        waive("hard", time.time() + 3600, "known issue, owner signed off")
        result = check({"rules": [{"name": "hard", "failed": True, "level": "block"}]})
        self.assertFalse(result["blocked"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["counts"]["block"], 0)
        self.assertEqual(result["waived"], ["hard"])
        self.assertEqual(result["active_waivers"][0]["rule"], "hard")
        self.assertEqual(result["active_waivers"][0]["reason"], "known issue, owner signed off")

    def test_expired_waiver_is_inactive_and_audited(self):
        record = waive("hard", time.time() - 1, "late cleanup")
        self.assertFalse(record["active"])
        result = check({"rules": [{"name": "hard", "failed": True, "level": "block"}]})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["active_waivers"], [])
        events = [entry["event"] for entry in audit()]
        self.assertIn("waived", events)
        self.assertIn("expired", events)

    def test_waiver_expires_during_check_cycle(self):
        waive("w", time.time() + 3600, "temporary")
        from qualitygate import gate as gate_module
        original_now = gate_module._now
        try:
            gate_module._now = lambda: time.time() + 7200
            result = check({"rules": [{"name": "w", "failed": True, "level": "warning", "failed_rows": 5000}]})
            self.assertTrue(result["blocked"])
            self.assertEqual(result["waived"], [])
            self.assertIn("expired", [entry["event"] for entry in audit()])
        finally:
            gate_module._now = original_now

    def test_repeated_check_is_consistent(self):
        waive("w", time.time() + 3600, "temp")
        batch = {
            "rules": [
                {"name": "w", "failed": True, "level": "warning", "failed_rows": 5},
                {"name": "i", "failed": True, "level": "info"},
                {"name": "b", "failed": True, "level": "block"},
            ]
        }
        first = check(batch)
        second = check(batch)
        self.assertEqual(first, second)
        self.assertEqual(second["counts"], {"block": 1, "warning": 0, "info": 1})
