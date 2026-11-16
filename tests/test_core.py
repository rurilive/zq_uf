import unittest

from forensics import by_source, report, timeline

EVIDENCE = [
    {"id": "f1", "time": "2024-05-01T09:30+08:00", "source": "file", "subject": "u1", "action": "read"},
    {"id": "l1", "time": "2024-05-01T02:00+00:00", "source": "log", "subject": "u1", "action": "login"},
    {"id": "r1", "time": "2024-05-01T02:00:04+00:00", "source": "registry", "subject": "u1", "action": "login"},
]


class TimelineTest(unittest.TestCase):
    def test_order_follows_normalized_instant(self):
        out = timeline(EVIDENCE)
        # f1 归一到 01:30Z，早于 02:00Z 的登录段
        self.assertEqual([item["id"] for item in out["events"]], ["f1", "l1"])
        self.assertEqual(out["events"][0]["time"], "2024-05-01T01:30:00Z")

    def test_original_time_kept_alongside_normalized(self):
        out = timeline(EVIDENCE)
        merged = out["events"][1]
        raws = [entry["raw"] for entry in merged["times"]]
        self.assertIn("2024-05-01T02:00:04+00:00", raws)
        self.assertEqual(merged["records"][0]["time"], "2024-05-01T02:00+00:00")

    def test_nearby_events_merged_keeping_all_sources(self):
        out = timeline(EVIDENCE)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["merged"], 1)
        merged = out["events"][1]
        self.assertEqual(merged["sources"], ["log", "registry"])
        self.assertEqual(merged["ids"], ["l1", "r1"])
        self.assertEqual(merged["skew"], 4.0)
        self.assertFalse(merged["conflict"])
        self.assertEqual(out["conflicts"], [])

    def test_skew_beyond_threshold_listed_as_conflict(self):
        evidence = [
            {"id": "a", "time": "2024-05-01T02:00:00+00:00", "source": "log",
             "subject": "u1", "action": "login"},
            {"id": "b", "time": "2024-05-01T02:00:04+00:00", "source": "registry",
             "subject": "u1", "action": "login"},
            {"id": "c", "time": "2024-05-01T02:00:08+00:00", "source": "file",
             "subject": "u1", "action": "login"},
        ]
        out = timeline(evidence)
        self.assertEqual(out["count"], 1)
        self.assertEqual(len(out["conflicts"]), 1)
        conflict = out["conflicts"][0]
        self.assertEqual(conflict["skew"], 8.0)
        self.assertEqual([entry["source"] for entry in conflict["entries"]],
                         ["log", "registry", "file"])
        # 并列展示原始值与归一值
        self.assertEqual(conflict["entries"][0]["raw"], "2024-05-01T02:00:00+00:00")
        self.assertEqual(conflict["entries"][0]["normalized"], "2024-05-01T02:00:00Z")

    def test_distinct_subjects_not_merged(self):
        evidence = [
            {"id": "a", "time": "2024-05-01T02:00:00+00:00", "source": "log",
             "subject": "u1", "action": "login"},
            {"id": "b", "time": "2024-05-01T02:00:01+00:00", "source": "log",
             "subject": "u2", "action": "login"},
        ]
        self.assertEqual(timeline(evidence)["count"], 2)

    def test_missing_time_marked_untimed(self):
        evidence = EVIDENCE + [{"id": "x1", "source": "file", "action": "read"},
                               {"id": "x2", "time": "not-a-time", "source": "log"}]
        out = timeline(evidence)
        self.assertEqual(len(out["untimed"]), 2)
        self.assertEqual(out["untimed"][0]["reason"], "缺少时间")
        self.assertEqual(out["untimed"][1]["reason"], "时间无法解析")
        self.assertEqual(out["count"], 2)

    def test_naive_time_assumed_utc_and_flagged(self):
        out = timeline([{"id": "n1", "time": "2024-05-01 02:00:00", "source": "log"}])
        event = out["events"][0]
        self.assertEqual(event["time"], "2024-05-01T02:00:00Z")
        self.assertTrue(event["assumed_timezone"])

    def test_case_evidence_merges_across_calls(self):
        timeline([EVIDENCE[0]], case="c1")
        out = timeline([EVIDENCE[1], EVIDENCE[2]], case="c1")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["case"], "c1")
        rep = report(case="c1")
        self.assertEqual(rep["events"], 2)
        self.assertEqual(rep["sources"], {"file": 1, "log": 1, "registry": 1})

    def test_report_summary(self):
        timeline(EVIDENCE)
        rep = report()
        self.assertEqual(rep["events"], 2)
        self.assertEqual(rep["sources"], {"file": 1, "log": 1, "registry": 1})
        self.assertEqual(rep["merged"], 1)
        self.assertEqual(rep["conflicts"], 0)
        self.assertEqual(rep["skew"], 4.0)
        self.assertEqual(rep["untimed"], 0)

    def test_by_source_filters_merged_events(self):
        timeline(EVIDENCE)
        hits = by_source("registry")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["sources"], ["log", "registry"])
        self.assertEqual(hits[0]["times"][1]["raw"], "2024-05-01T02:00:04+00:00")
        self.assertEqual(len(by_source("file")), 1)


if __name__ == "__main__":
    unittest.main()
