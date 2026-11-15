import json
import unittest

from crawlreport import REPORT_VERSION, build, drilldown, export, reconcile


class ReportTest(unittest.TestCase):
    def test_build_counts_rows(self):
        self.assertEqual(build({"id": "b", "rows": [1, 2, 3]})["total"], 3)

    def test_build_has_no_breakdown(self):
        self.assertEqual(build({"id": "b", "rows": [1]})["by_site"], {})

    def test_reconcile_matches(self):
        self.assertTrue(reconcile({"batch": "b"})["matched"])


def _row(rid, site="a", stage="fetch", status="ok", reason="",
         time="2026-11-15T08:00:00"):
    row = {"id": rid, "site": site, "stage": stage, "status": status,
           "time": time}
    if reason:
        row["reason"] = reason
    return row


class StatusClassificationTest(unittest.TestCase):
    def test_failed_and_skipped_are_separate(self):
        report = build({"id": "b", "rows": [
            _row("1", status="ok"),
            _row("2", status="failed", reason="http_500"),
            _row("3", status="skipped", reason="duplicate"),
        ]})
        self.assertEqual(report["by_status"]["ok"], 1)
        self.assertEqual(report["by_status"]["failed"], 1)
        self.assertEqual(report["by_status"]["skipped"], 1)
        self.assertEqual([e["id"] for e in report["failures"]], ["2"])
        self.assertEqual([e["id"] for e in report["skips"]], ["3"])

    def test_by_site_and_stage(self):
        report = build({"id": "b", "rows": [
            _row("1", site="a", stage="fetch"),
            _row("2", site="a", stage="parse", status="failed", reason="x"),
            _row("3", site="b", stage="fetch"),
        ]})
        self.assertEqual(report["by_site"]["a"]["total"], 2)
        self.assertEqual(report["by_site"]["a"]["failed"], 1)
        self.assertEqual(report["by_site"]["b"]["ok"], 1)
        self.assertEqual(report["by_stage"]["fetch"]["total"], 2)
        self.assertEqual(report["by_stage"]["parse"]["failed"], 1)
        self.assertEqual(report["by_reason"], {"x": 1})


class ReconcileTest(unittest.TestCase):
    def test_matched_when_store_matches(self):
        rows = [_row("1"), _row("2", site="b")]
        report = build({"id": "b", "rows": rows, "stored": rows})
        result = reconcile(report)
        self.assertTrue(result["matched"])
        self.assertEqual(result["diff"], [])
        self.assertEqual(result["row_count"],
                         {"expected": 2, "actual": 2, "matched": True})
        self.assertTrue(result["checksum"]["matched"])

    def test_diff_missing_extra_mismatch(self):
        rows = [_row("1"), _row("2", site="b"),
                _row("3", site="b", stage="parse")]
        stored = [_row("1"),
                  _row("3", site="b", stage="parse", reason="changed"),
                  _row("9", site="c")]
        report = build({"id": "b", "rows": rows, "stored": stored})
        result = reconcile(report)
        self.assertFalse(result["matched"])
        reasons = {d["reason"] for d in result["diff"]}
        self.assertEqual(reasons, {"missing_in_store", "extra_in_store",
                                   "checksum_mismatch"})
        self.assertEqual(result["by_reason"]["missing_in_store"], 1)
        self.assertEqual(result["by_reason"]["extra_in_store"], 1)
        self.assertEqual(result["by_reason"]["checksum_mismatch"], 1)
        keys = [(d["site"], d["stage"], d["id"]) for d in result["diff"]]
        self.assertEqual(keys, sorted(keys))

    def test_reconcile_with_stored_argument(self):
        report = build({"id": "b", "rows": [_row("1"), _row("2")]})
        result = reconcile(report, stored=[_row("1"), _row("2")])
        self.assertTrue(result["matched"])

    def test_unreconcilable_rows_listed_separately(self):
        rows = [_row("1"), {"site": "a", "status": "ok"}]
        report = build({"id": "b", "rows": rows, "stored": [_row("1")]})
        result = reconcile(report)
        self.assertTrue(result["matched"])
        self.assertEqual(len(report["unreconcilable"]), 1)
        self.assertEqual(len(result["unreconcilable"]), 1)
        self.assertEqual(result["diff"], [])


class DrilldownTest(unittest.TestCase):
    def test_drilldown_by_site_and_reason(self):
        report = build({"id": "b", "rows": [
            _row("1", site="a", status="failed", reason="http_500"),
            _row("2", site="a", status="failed", reason="timeout"),
            _row("3", site="b", status="failed", reason="http_500"),
            _row("4", site="a"),
        ]})
        hits = drilldown(report, site="a", reason="http_500")
        self.assertEqual([h["id"] for h in hits], ["1"])
        self.assertEqual(len(drilldown(report, reason="http_500")), 2)
        self.assertEqual(len(drilldown(report, site="a", status="failed")), 2)

    def test_drilldown_by_time_range(self):
        report = build({"id": "b", "rows": [
            _row("1", time="2026-11-15T08:00:00"),
            _row("2", time="2026-11-15T10:00:00"),
        ]})
        hits = drilldown(report, since="2026-11-15T09:00:00")
        self.assertEqual([h["id"] for h in hits], ["2"])


class ResumeAndDeterminismTest(unittest.TestCase):
    ROWS = [_row("1"), _row("2", site="b"),
            _row("3", status="failed", reason="http_500"),
            {"site": "c"}, "raw-row"]
    NOW = "2026-11-15T09:00:00+00:00"

    def test_resume_after_interruption(self):
        part = build({"id": "b", "rows": self.ROWS[:2]}, now=self.NOW)
        resumed = build({"id": "b", "rows": self.ROWS[2:]},
                        resume_from=part, now=self.NOW)
        full = build({"id": "b", "rows": self.ROWS}, now=self.NOW)
        self.assertEqual(resumed, full)

    def test_deterministic_for_same_batch(self):
        first = build({"id": "b", "rows": self.ROWS}, now=self.NOW)
        second = build({"id": "b", "rows": self.ROWS}, now=self.NOW)
        self.assertEqual(first, second)


class ExportTest(unittest.TestCase):
    def test_export_has_version_and_generated_at(self):
        report = build({"id": "b", "rows": [_row("1")]},
                       now="2026-11-15T09:00:00+00:00")
        data = json.loads(export(report))
        self.assertEqual(data["version"], REPORT_VERSION)
        self.assertEqual(data["generated_at"], "2026-11-15T09:00:00+00:00")
