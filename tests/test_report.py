import json
import os
import tempfile
import unittest

from siteprobe import Samples, diff_reports, load_report, probe, save_report
from tests.fixtures import FakeFetcher, html_response, make_html

BASE = "http://report.test"
LIST = BASE + "/list"


def make_report(extra_signal=False):
    list_html = make_html("中文列表", [
        ("/list?page=2", "2"),
        ("/article/1.html", "详情"),
    ])
    if extra_signal:
        list_html += '<img id="captcha" src="/c.png">'
    pages = {
        BASE + "/": html_response(make_html("中文首页")),
        LIST: html_response(list_html),
        BASE + "/article/1.html": html_response(make_html("中文详情")),
    }
    return probe(BASE + "/", Samples(list_urls=[LIST], fetcher=FakeFetcher(pages), rate_per_sec=0))


class ReportTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        report = make_report()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested", "report.json")
            save_report(report, path)
            loaded = load_report(path)
            self.assertEqual(loaded["url"], report["url"])
            self.assertEqual(loaded["pages"], report["pages"])
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
            self.assertIn("中文", raw)  # ensure_ascii=False

    def test_diff_detects_site_change(self):
        old_report = make_report()
        new_report = make_report(extra_signal=True)
        diff = diff_reports(old_report, new_report)
        self.assertTrue(diff["changed"])
        groups = {item["group"] for item in diff["signals_changed"]}
        self.assertIn("captcha", groups)
        self.assertTrue(any(item["field"] == "classification" for item in diff["classification_changed"]))

    def test_diff_ignores_timestamp_and_reports_no_change(self):
        report_a = make_report()
        report_b = json.loads(json.dumps(report_a))
        report_b["generated_at"] = "2099-01-01T00:00:00Z"
        diff = diff_reports(report_a, report_b)
        self.assertFalse(diff["changed"])

    def test_diff_detects_page_metric_change(self):
        old_report = make_report()
        new_report = make_report()
        new_report["pages"][0]["status"] = 404
        diff = diff_reports(old_report, new_report)
        self.assertTrue(diff["pages_changed"])
        changed_fields = {item["field"] for item in diff["pages_changed"][0]["changes"]}
        self.assertIn("status", changed_fields)


if __name__ == "__main__":
    unittest.main()
