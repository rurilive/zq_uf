import unittest

from alertfold import emit, report, reset


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_each_alert_kept(self):
        out = emit([
            {"reason": "disk full", "host": "db-01", "time": "10:01"},
            {"reason": "disk full", "host": "db-02", "time": "10:02"},
        ])
        self.assertEqual(out["accepted"], 2)
        self.assertEqual(len(report()["entries"]), 2)

    def test_same_host_counted_twice(self):
        emit([
            {"reason": "cpu high", "host": "web-01", "time": "09:10"},
            {"reason": "cpu high", "host": "web-01", "time": "09:20"},
        ])
        self.assertEqual(report()["hosts"], 2)

    def test_by_reason_counts_entries(self):
        emit([
            {"reason": "disk full", "host": "db-01"},
            {"reason": "disk full", "host": "db-02"},
            {"reason": "tls expired", "host": "gw-01"},
        ])
        self.assertEqual(report()["by_reason"], {"disk full": 2, "tls expired": 1})

    def test_resolved_alert_stored_separately(self):
        emit([{"reason": "disk full", "host": "db-01", "status": "resolved", "time": "11:00"}])
        entries = report()["entries"]
        self.assertEqual(entries[0]["status"], "resolved")
        self.assertEqual(len(entries), 1)

    def test_missing_host_raises(self):
        with self.assertRaises(ValueError):
            emit([{"reason": "disk full"}])


if __name__ == "__main__":
    unittest.main()
