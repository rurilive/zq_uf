import unittest

from sessionaudit import sweep, use


class AuditTest(unittest.TestCase):
    def test_use_creates_session(self):
        self.assertTrue(use("acc-1")["session"].startswith("s-acc-1-"))

    def test_use_has_no_audit(self):
        self.assertEqual(use("acc-2")["audit"], [])

    def test_sweep_reclaims_nothing(self):
        self.assertEqual(sweep(100)["reclaimed"], [])
