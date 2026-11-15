import json
import os
import tempfile
import unittest

from seedscore import ReviewLog, diff_reports, filter
from seedscore.export import export_changes, export_report, export_scores


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = filter(
            [
                {"url": "https://a.example/", "source": "manual"},
                {"url": "https://a.example/?utm_source=x", "source": "crawl"},
                {"url": "https://b.example/login/cart/1/2/3/4/5", "source": "referral"},
                {"url": "https://c.example/", "status": "dead"},
            ]
        )

    def test_export_report_jsonl_one_row_per_seed(self):
        text = export_report(self.report)
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            sorted({row["status"] for row in rows}), ["dead", "dropped", "kept"]
        )

    def test_export_report_csv_has_component_columns(self):
        text = export_report(self.report, format="csv")
        header = text.splitlines()[0].split(",")
        for column in ("score", "source_credibility", "history_yield", "link_quality"):
            self.assertIn(column, header)

    def test_export_report_without_dead(self):
        text = export_report(self.report, format="jsonl", include_dead=False)
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertNotIn("dead", {row["status"] for row in rows})

    def test_export_report_to_file(self):
        path = os.path.join(self.tmp.name, "report.csv")
        export_report(self.report, path, format="csv")
        self.assertTrue(os.path.exists(path))

    def test_export_scores(self):
        records = self.report["kept"] + self.report["dropped"]
        text = export_scores(records)
        self.assertEqual(len(text.strip().splitlines()), len(records))

    def test_export_changes(self):
        first = filter([{"url": "https://a.example/", "source": "manual"}])
        second = filter(
            [
                {"url": "https://a.example/", "source": "manual", "history": {"successes": 9, "attempts": 10}},
                {"url": "https://new.example/", "source": "manual"},
            ],
            previous=first,
        )
        text = export_changes(second["changes"])
        kinds = {json.loads(line)["kind"] for line in text.splitlines()}
        self.assertIn("added", kinds)
        self.assertIn("score_change", kinds)

    def test_export_restores_csv(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="false kill")
        text = log.export(format="csv")
        self.assertIn("a.example", text)


class DiffTest(unittest.TestCase):
    def test_orders_and_changes(self):
        seeds = [
            {"url": "https://a.example/", "source": "manual"},
            {"url": "https://b.example/", "source": "manual"},
        ]
        first = filter(seeds)
        second = filter(
            [
                {"url": "https://a.example/", "source": "manual", "history": {"successes": 9, "attempts": 10}},
                {"url": "https://b.example/", "source": "manual"},
            ],
            queue=[item["target"] for item in first["kept"]],
            previous=first,
        )
        changes = diff_reports(first, second)
        self.assertEqual(changes["counts"]["added"], 0)
        self.assertEqual(changes["counts"]["removed"], 0)
        self.assertEqual(len(changes["score_changes"]), 1)
        self.assertGreater(changes["score_changes"][0]["delta"], 0)
        self.assertFalse(changes["order_changed"])
        self.assertIn("a.example", changes["by_site"])

    def test_added_and_removed(self):
        first = filter([{"url": "https://a.example/", "source": "manual"}])
        second = filter([{"url": "https://b.example/", "source": "manual"}])
        changes = diff_reports(first, second)
        self.assertEqual(changes["added"], ["b.example/"])
        self.assertEqual(changes["removed"], ["a.example/"])

    def test_threshold_crossing_is_flagged(self):
        seed = {"url": "https://a.example/", "source": "manual"}
        first = filter([seed])
        second = filter([seed], {"threshold": 0.95}, queue=[item["target"] for item in first["kept"]])
        changes = diff_reports(first, second)
        self.assertTrue(changes["score_changes"])
        self.assertTrue(changes["score_changes"][0]["crossed_threshold"])


if __name__ == "__main__":
    unittest.main()
