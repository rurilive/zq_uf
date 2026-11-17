import unittest

from crashmin import minimize, reproduce
from crashmin import delta

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}


class DeltaTest(unittest.TestCase):
    def setUp(self):
        delta._samples.clear()

    def test_minimal_sample_still_crashes(self):
        result = minimize(CRASH)
        self.assertEqual(result["data"], "CRASH")
        self.assertEqual(result["size"], 5)

    def test_ratio_is_reported(self):
        self.assertEqual(minimize(CRASH)["ratio"], 0.875)

    def test_original_is_kept(self):
        result = minimize(CRASH)
        self.assertEqual(result["original"], "samples/c1/original")
        self.assertEqual(delta._samples["c1"]["original"], CRASH["data"])

    def test_every_step_records_interval_and_crash_state(self):
        result = minimize(CRASH)
        self.assertTrue(result["steps"])
        for step in result["steps"]:
            self.assertIn("start", step)
            self.assertIn("end", step)
            self.assertIn("crashed", step)

    def test_reproduce_prefers_minimal_and_has_command(self):
        minimize(CRASH)
        result = reproduce("c1")
        self.assertTrue(result["found"])
        self.assertEqual(result["data"], "CRASH")
        self.assertIn("c1", result["command"])
        self.assertEqual(result["original"], "samples/c1/original")

    def test_reproduce_missing_id(self):
        result = reproduce("c404")
        self.assertFalse(result["found"])
        self.assertIsNone(result["data"])

    def test_missing_trigger_is_rejected(self):
        with self.assertRaises(ValueError):
            minimize({"id": "c2", "data": "AAAA", "trigger": ""})

    def test_original_not_crashing_is_rejected(self):
        with self.assertRaises(ValueError):
            minimize({"id": "c3", "data": "AAAA", "trigger": "CRASH"})

    def test_hint_when_nearly_minimal(self):
        # 405 字节的输入里 trigger 占 401，删 1 字节后删减比例就低于阈值
        near = {"id": "c5", "data": "CRASH" + "A" * 400, "trigger": "CRASH" + "A" * 396}
        result = minimize(near)
        self.assertEqual(result["hint"], "删减比例过低，输入可能已经接近最小")
        self.assertLess(result["ratio"], 0.01)


if __name__ == "__main__":
    unittest.main()
