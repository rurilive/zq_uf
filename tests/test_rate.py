import unittest

from rateadapt import acquire, feedback


class RateTest(unittest.TestCase):
    def test_rate_is_fixed(self):
        self.assertEqual(acquire("a")["rate"], 1.0)

    def test_acquire_always_ok(self):
        self.assertTrue(acquire("b")["ok"])

    def test_feedback_does_not_adjust(self):
        self.assertFalse(feedback("a", 429, 2.0)["adjusted"])
