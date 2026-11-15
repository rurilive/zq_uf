import unittest

from detectsoft import configure, judge, report, reset


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


class ContentSignalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_empty_body_marked_but_single_page_stays_ok(self):
        r = judge({"url": "https://c/1", "site": "c", "status": 200,
                   "text": "", "template": "item"})
        self.assertEqual(r["kind"], "ok")
        self.assertIn("empty_body", r["marks"])

    def test_placeholder_text_marked(self):
        r = judge({"url": "https://c/2", "site": "c", "status": 200,
                   "text": "暂无内容，请稍后再来", "template": "item"})
        self.assertIn("placeholder_text", r["marks"])
        self.assertEqual(r["evidence"]["placeholder_text"], "暂无内容")

    def test_abnormal_redirect_marked(self):
        r = judge({"url": "https://c/3", "site": "c", "status": 200,
                   "text": "x" * 50, "redirect": "https://c/",
                   "template": "item"})
        self.assertIn("abnormal_redirect", r["marks"])
        self.assertEqual(r["evidence"]["abnormal_redirect"]["reason"],
                         "redirect_to_root")

    def test_consecutive_hits_escalate_to_soft404(self):
        page = {"site": "c", "status": 200, "text": "", "template": "gone"}
        kinds = [judge(dict(page, url="https://c/g%d" % i))["kind"]
                 for i in range(3)]
        self.assertEqual(kinds, ["ok", "ok", "soft404"])
        r = judge(dict(page, url="https://c/g3"))
        self.assertEqual(r["kind"], "soft404")  # 模板已确认，命中即升级
        self.assertIn("template_pattern", r["marks"])
        self.assertEqual(len(r["samples"]), 4)  # 连续命中的样本都作证据

    def test_normal_page_resets_consecutive_count(self):
        page = {"site": "c", "status": 200, "text": "", "template": "t2"}
        judge(dict(page, url="https://c/t0"))
        judge(dict(page, url="https://c/t1"))
        judge({"url": "https://c/ok", "site": "c", "status": 200,
               "text": "真实商品内容" * 20, "template": "t2"})
        r = judge(dict(page, url="https://c/t2"))
        self.assertEqual(r["kind"], "ok")  # 连续被打断，重新计数


class TemplateStatsTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _seed(self):
        for i in range(3):
            judge({"url": "https://d/g%d" % i, "site": "d", "status": 200,
                   "text": "", "template": "gone", "day": "2026-11-14",
                   "headers": {"x-server": "nginx"}})
        judge({"url": "https://d/ok", "site": "d", "status": 200,
               "text": "正常内容" * 30, "template": "item",
               "day": "2026-11-15"})
        judge({"url": "https://d/404", "site": "d", "status": 404,
               "template": "item", "day": "2026-11-15"})

    def test_report_by_day(self):
        self._seed()
        r = report("d", by="day")
        self.assertEqual(r["days"]["2026-11-14"],
                         {"total": 3, "soft404": 1, "ratio": round(1 / 3, 4)})
        self.assertEqual(r["days"]["2026-11-15"],
                         {"total": 2, "soft404": 1, "ratio": 0.5})

    def test_report_by_template(self):
        self._seed()
        r = report("d", by="template")
        gone = r["templates"]["gone"]
        self.assertEqual(gone["total"], 3)
        self.assertEqual(gone["soft404"], 1)
        self.assertEqual(gone["flagged"], 3)
        self.assertTrue(gone["confirmed"])
        self.assertEqual(r["templates"]["item"]["soft404"], 1)

    def test_samples_keep_url_headers_and_snippet(self):
        self._seed()
        samples = report("d", by="template")["templates"]["gone"]["samples"]
        self.assertTrue(samples)
        first = samples[0]
        self.assertEqual(first["url"], "https://d/g0")
        self.assertEqual(first["headers"], {"x-server": "nginx"})
        self.assertIn("text_snippet", first)
        self.assertEqual(first["day"], "2026-11-14")


class ThresholdConfigTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_per_site_threshold_override(self):
        configure("e", escalate_after=2)
        page = {"site": "e", "status": 200, "text": "", "template": "t"}
        self.assertEqual(judge(dict(page, url="https://e/1"))["kind"], "ok")
        self.assertEqual(judge(dict(page, url="https://e/2"))["kind"],
                         "soft404")
        # 未覆盖的站点仍用默认阈值 3
        other = {"site": "f", "status": 200, "text": "", "template": "t"}
        self.assertEqual(judge(dict(other, url="https://f/1"))["kind"], "ok")
        self.assertEqual(judge(dict(other, url="https://f/2"))["kind"], "ok")

    def test_allowlist_rolls_back_false_positive(self):
        page = {"site": "g", "status": 200, "text": "", "template": "sparse"}
        for i in range(3):
            judge(dict(page, url="https://g/%d" % i))
        self.assertEqual(report("g")["soft404"], 1)
        reset()
        configure("g", allow_templates=["sparse"])
        for i in range(3):
            r = judge(dict(page, url="https://g/%d" % i))
            self.assertEqual(r["kind"], "ok")
        self.assertEqual(report("g")["soft404"], 0)

    def test_unknown_config_key_rejected(self):
        with self.assertRaises(ValueError):
            configure("h", no_such_key=1)
