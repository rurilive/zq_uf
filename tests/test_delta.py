import unittest

from crashmin import minimize, reproduce

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}


class DeltaTest(unittest.TestCase):
    def test_minimal_sample_still_crashes(self):
        result = minimize(CRASH)
        self.assertEqual(result["data"], "CRASH")
        self.assertEqual(result["size"], 5)
        self.assertEqual(result["ratio"], 0.875)

    def test_original_is_kept(self):
        result = minimize(CRASH)
        self.assertEqual(result["original"], "crashmin://samples/c1/original")

    def test_steps_record_intervals_and_crash_state(self):
        steps = minimize(CRASH)["steps"]
        self.assertTrue(steps)
        for step in steps:
            self.assertIn("start", step)
            self.assertIn("end", step)
            self.assertIn("crashes", step)
        # 至少有一步删减后崩溃仍在，也有一步回退。
        self.assertTrue(any(s["crashes"] for s in steps))
        self.assertTrue(any(not s["crashes"] for s in steps))

    def test_reproduce_uses_minimal_sample_and_has_command(self):
        minimize(CRASH)
        result = reproduce("c1")
        self.assertTrue(result["found"])
        self.assertEqual(result["data"], "CRASH")
        self.assertTrue(result["crashes"])
        self.assertIn("reproduce('c1')", result["command"])

    def test_reproduce_missing_id(self):
        result = reproduce("c404")
        self.assertFalse(result["found"])
        self.assertIsNone(result["data"])
        self.assertIsNone(result["command"])

    def test_near_minimal_input_gets_hint(self):
        result = minimize({"id": "c2", "data": "CRASH", "trigger": "CRASH"})
        self.assertEqual(result["ratio"], 0.0)
        self.assertIsNotNone(result["hint"])

    def test_missing_trigger_is_rejected(self):
        with self.assertRaises(ValueError):
            minimize({"id": "c3", "data": "AAAA", "trigger": ""})


if __name__ == "__main__":
    unittest.main()
