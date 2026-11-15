import unittest
from unittest import mock

import metriclate2.metric as m
from metriclate2 import add, recompute, reset, versions


class MetricTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_add_accumulates(self):
        add({"window": "w1", "value": 2})
        self.assertEqual(add({"window": "w1", "value": 3})["sum"], 5.0)

    def test_late_event_goes_back_to_event_time_window(self):
        add({"event_time": "2026-11-15T10:01:00", "value": 2.0})
        late = add({"event_time": "2026-11-15T09:30:00", "value": 3.0})
        self.assertEqual(late["window"], "2026-11-15T09")
        self.assertEqual(recompute("2026-11-15T10")["sum"], 2.0)

    def test_recompute_retracts_then_writes(self):
        add({"window": "w2", "value": 1})
        r = recompute("w2")
        self.assertTrue(r["recomputed"])
        self.assertEqual(r["retracted"], {"version": 1, "count": 1, "sum": 1.0})
        self.assertEqual(r["version"], 2)

    def test_recompute_idempotent(self):
        add({"window": "w3", "value": 1.5})
        add({"window": "w3", "value": 2.5})
        first = recompute("w3")
        second = recompute("w3")
        self.assertEqual((first["count"], first["sum"]),
                         (second["count"], second["sum"]))

    def test_versions_keep_retraction_details(self):
        add({"window": "w4", "value": 1})
        add({"window": "w4", "value": 2})
        recompute("w4")
        history = versions("w4")
        self.assertEqual(len(history), 3)
        self.assertIsNone(history[0]["retracted"])
        self.assertEqual(history[1]["retracted"]["sum"], 1.0)
        self.assertEqual(history[2]["retracted"]["sum"], 3.0)
        self.assertEqual([v["version"] for v in history], [1, 2, 3])

    def test_failure_rolls_back_to_previous_version(self):
        add({"window": "w5", "value": 1})
        before = recompute("w5")
        with mock.patch.object(m, "_aggregate", side_effect=RuntimeError("boom")):
            self.assertRaises(RuntimeError, recompute, "w5")
        after = versions("w5")
        self.assertEqual(len(after), before["version"])
        self.assertEqual(recompute("w5")["sum"], 1.0)

    def test_response_fields(self):
        r = add({"window": "w6", "value": 1})
        for key in ("window", "recomputed", "retracted", "version"):
            self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
