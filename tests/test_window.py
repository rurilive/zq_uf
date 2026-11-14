import os
import tempfile
import unittest

from metricdedup import flush, put
from metricdedup import dedup


class WindowDedupTest(unittest.TestCase):
    def setUp(self):
        self._cfg = (dedup._window_seconds, dedup._max_size, dedup._state_path)
        dedup._state_path = None
        dedup._window_seconds = 60
        dedup._max_size = 10000
        dedup.reset()

    def tearDown(self):
        dedup._window_seconds, dedup._max_size, dedup._state_path = self._cfg

    def test_same_key_same_ts_duplicate(self):
        put({"name": "cpu", "ts": 10})
        r = put({"name": "cpu", "ts": 10})
        self.assertTrue(r["duplicate"])
        self.assertFalse(r["written"])
        self.assertEqual(flush()["written"], 1)

    def test_same_key_newer_ts_written(self):
        put({"name": "cpu", "ts": 10})
        self.assertTrue(put({"name": "cpu", "ts": 20})["written"])
        self.assertEqual(dedup._seen["cpu"], 20)

    def test_out_of_order_keeps_latest(self):
        put({"name": "cpu", "ts": 20})
        r = put({"name": "cpu", "ts": 10})
        self.assertFalse(r["written"])
        self.assertTrue(r["duplicate"])
        self.assertEqual(dedup._seen["cpu"], 20)

    def test_window_cleanup(self):
        dedup._window_seconds = 10
        put({"name": "old", "ts": 1})
        put({"name": "new", "ts": 100})
        self.assertNotIn("old", dedup._seen)
        self.assertEqual(flush()["expired"], 1)
        self.assertEqual(flush()["size"], 1)

    def test_late_metric_expired_not_written(self):
        dedup._window_seconds = 10
        put({"name": "a", "ts": 100})
        r = put({"name": "b", "ts": 1})
        self.assertFalse(r["written"])
        self.assertTrue(r["expired"])
        self.assertNotIn("b", dedup._seen)
        self.assertEqual(flush()["expired"], 1)

    def test_max_size_evicts_oldest_first(self):
        dedup._max_size = 2
        put({"name": "a", "ts": 1})
        put({"name": "b", "ts": 2})
        put({"name": "c", "ts": 3})
        self.assertEqual(flush()["size"], 2)
        self.assertNotIn("a", dedup._seen)
        self.assertIn("b", dedup._seen)
        self.assertIn("c", dedup._seen)
        self.assertEqual(flush()["expired"], 1)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            dedup._state_path = os.path.join(d, "state.json")
            put({"name": "cpu", "ts": 5})
            put({"name": "cpu", "ts": 5})
            snap = flush()
            dedup.reset()
            self.assertEqual(flush()["size"], 0)
            dedup.load_state()
            self.assertEqual(flush(), snap)
            self.assertEqual(dedup._seen["cpu"], 5)


if __name__ == "__main__":
    unittest.main()
