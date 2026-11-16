import unittest

from forensics import by_source, report, timeline

EVIDENCE = [
    {"id": "f1", "time": "2024-05-01T09:30+08:00", "source": "file", "subject": "u1", "action": "read"},
    {"id": "l1", "time": "2024-05-01T02:00+00:00", "source": "log", "subject": "u1", "action": "login"},
    {"id": "r1", "time": "2024-05-01T02:00:04+00:00", "source": "registry", "subject": "u1", "action": "login"},
]


class TimelineTest(unittest.TestCase):
    def test_order_follows_raw_string_not_instant(self):
        out = timeline(EVIDENCE)
        self.assertEqual([item["id"] for item in out["events"]], ["l1", "r1", "f1"])

    def test_original_time_kept_untouched(self):
        out = timeline(EVIDENCE)
        self.assertEqual(out["events"][2]["time"], "2024-05-01T09:30+08:00")

    def test_nearby_events_not_merged(self):
        out = timeline(EVIDENCE)
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["merged"], 0)
        self.assertEqual(out["conflicts"], [])

    def test_report_counts_per_source(self):
        timeline(EVIDENCE)
        rep = report()
        self.assertEqual(rep["events"], 3)
        self.assertEqual(rep["sources"], {"file": 1, "log": 1, "registry": 1})
        self.assertEqual(rep["skew"], 0)

    def test_by_source_filters_without_conversion(self):
        timeline(EVIDENCE)
        self.assertEqual(len(by_source("log")), 1)
        self.assertEqual(by_source("log")[0]["time"], "2024-05-01T02:00+00:00")


if __name__ == "__main__":
    unittest.main()
