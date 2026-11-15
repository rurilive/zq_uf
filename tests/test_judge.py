import unittest

from detectsoft import judge, report


class JudgeTest(unittest.TestCase):
    def test_status_200_is_ok(self):
        page = {"url": "https://a/1", "site": "a", "status": 200, "text": ""}
        self.assertEqual(judge(page)["kind"], "ok")

    def test_other_status_is_soft404(self):
        page = {"url": "https://a/2", "site": "a", "status": 404, "text": "missing"}
        self.assertEqual(judge(page)["kind"], "soft404")

    def test_report_counts(self):
        judge({"url": "https://b/1", "site": "b", "status": 200})
        judge({"url": "https://b/2", "site": "b", "status": 500})
        self.assertEqual(report("b"), {"site": "b", "total": 2, "soft404": 1})
