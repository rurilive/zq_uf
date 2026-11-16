import unittest

from crashmin import minimize, reproduce

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}


class DeltaTest(unittest.TestCase):
    def test_size_is_a_quarter(self):
        self.assertEqual(minimize(CRASH)["size"], 10)

    def test_ratio_is_reported(self):
        self.assertEqual(minimize(CRASH)["ratio"], 0.75)

    def test_original_is_not_kept(self):
        self.assertIsNone(minimize(CRASH)["original"])

    def test_reproduce_has_no_command(self):
        minimize(CRASH)
        result = reproduce("c1")
        self.assertTrue(result["found"])
        self.assertIsNone(result["command"])

    def test_missing_trigger_is_rejected(self):
        with self.assertRaises(ValueError):
            minimize({"id": "c2", "data": "AAAA", "trigger": ""})
