import unittest

from alertfold import emit, report, reset, split


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_reason_merged_into_one_entry(self):
        out = emit([
            {"reason": "disk full", "host": "db-01", "site": "bj", "time": "10:01"},
            {"reason": "disk full", "host": "db-02", "site": "bj", "time": "10:02"},
        ])
        self.assertEqual(out["accepted"], 2)
        entries = report()["entries"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["host_count"], 2)
        self.assertEqual(entry["first_time"], "10:01")
        self.assertEqual(entry["last_time"], "10:02")
        self.assertEqual(entry["status"], "firing")

    def test_same_host_deduplicated(self):
        emit([
            {"reason": "cpu high", "host": "web-01", "time": "09:10"},
            {"reason": "cpu high", "host": "web-01", "time": "09:20"},
        ])
        entry = report()["entries"][0]
        self.assertEqual(entry["host_count"], 1)
        self.assertEqual(entry["alert_count"], 2)
        self.assertEqual(report()["hosts"], 1)

    def test_by_reason_counts_merged_entries(self):
        emit([
            {"reason": "disk full", "host": "db-01"},
            {"reason": "disk full", "host": "db-02"},
            {"reason": "tls expired", "host": "gw-01"},
        ])
        self.assertEqual(report()["by_reason"], {"disk full": 1, "tls expired": 1})

    def test_first_and_last_alert_kept(self):
        emit([
            {"reason": "disk full", "host": "db-01", "time": "10:01"},
            {"reason": "disk full", "host": "db-02", "time": "10:02"},
            {"reason": "disk full", "host": "db-03", "time": "10:03"},
        ])
        entry = report()["entries"][0]
        self.assertEqual(entry["first_alert"]["host"], "db-01")
        self.assertEqual(entry["last_alert"]["host"], "db-03")
        self.assertEqual(len(entry["alerts"]), 3)

    def test_resolved_closes_entry(self):
        emit([{"reason": "disk full", "host": "db-01", "time": "10:01"}])
        emit([{"reason": "disk full", "host": "db-01", "status": "resolved", "time": "11:00"}])
        entries = report()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["status"], "resolved")
        self.assertEqual(entries[0]["last_time"], "11:00")

    def test_recurrence_after_resolve_opens_new_entry(self):
        emit([{"reason": "disk full", "host": "db-01", "time": "10:01"}])
        emit([{"reason": "disk full", "host": "db-01", "status": "resolved", "time": "11:00"}])
        emit([{"reason": "disk full", "host": "db-02", "time": "23:30"}])
        rep = report()
        self.assertEqual(rep["by_reason"], {"disk full": 2})
        self.assertEqual([e["status"] for e in rep["entries"]], ["resolved", "firing"])
        self.assertEqual(rep["entries"][1]["hosts"], ["db-02"])

    def test_impact_listed_by_site(self):
        emit([
            {"reason": "disk full", "host": "db-01", "site": "bj"},
            {"reason": "disk full", "host": "db-02", "site": "bj"},
            {"reason": "disk full", "host": "db-03", "site": "sh"},
        ])
        sites = report()["entries"][0]["sites"]
        self.assertEqual(sites, {"bj": ["db-01", "db-02"], "sh": ["db-03"]})

    def test_split_restores_individual_entries(self):
        emit([
            {"reason": "disk full", "host": "db-01", "time": "10:01"},
            {"reason": "disk full", "host": "db-02", "time": "10:02"},
        ])
        entry_id = report()["entries"][0]["id"]
        out = split(entry_id)
        self.assertEqual(out["split"], entry_id)
        self.assertEqual(len(out["created"]), 2)
        entries = report()["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual([e["host_count"] for e in entries], [1, 1])

    def test_split_unknown_entry_raises(self):
        with self.assertRaises(ValueError):
            split(999)

    def test_missing_host_raises(self):
        with self.assertRaises(ValueError):
            emit([{"reason": "disk full"}])


if __name__ == "__main__":
    unittest.main()
