import unittest

from qualitygate import check, waive


class GateTest(unittest.TestCase):
    def test_any_failure_blocks(self):
        result = check({"rules": [{"name": "a", "failed": False}, {"name": "b", "failed": True}]})
        self.assertTrue(result["blocked"])

    def test_no_severity(self):
        result = check({"rules": [{"name": "a", "failed": True}]})
        self.assertNotIn("levels", result)

    def test_waive_not_applied(self):
        self.assertFalse(waive("a", 100, "known")["active"])
