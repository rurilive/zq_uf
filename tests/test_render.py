import unittest

from jsrender import wait


class RenderTest(unittest.TestCase):
    def test_condition_true(self):
        self.assertTrue(wait({"ready": True}, lambda p: p["ready"])["ok"])

    def test_condition_false(self):
        self.assertFalse(wait({"ready": False}, lambda p: p["ready"])["ok"])

    def test_fixed_wait(self):
        self.assertEqual(wait({}, lambda p: True)["waited"], 2.0)
