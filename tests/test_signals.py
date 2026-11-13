import unittest

from siteprobe import Samples, probe
from tests.fixtures import FakeFetcher, html_response, make_html, timeout_response

BASE = "http://signal.test"
LIST = BASE + "/list"


def run(pages, list_urls=None):
    return probe(BASE + "/", Samples(
        list_urls=list_urls or [LIST],
        fetcher=FakeFetcher(pages),
        rate_per_sec=0,
    ))


class SignalTest(unittest.TestCase):
    def test_login_redirect_detected(self):
        pages = {
            BASE + "/": html_response("", status=302, final_url=BASE + "/login",
                                      redirects=[{"status": 302, "url": BASE + "/login"}]),
            LIST: html_response(make_html("登录", [("/login", "请登录")]),
                                final_url=BASE + "/login"),
        }
        report = run(pages)
        self.assertTrue(report["signals"]["login_required"])
        self.assertEqual(report["assessment"]["classification"], "login_required")

    def test_login_text_detected(self):
        pages = {
            BASE + "/": html_response(make_html("首页")),
            LIST: html_response(make_html("列表", extra="<p>请先登录后查看</p>")),
        }
        report = run(pages)
        self.assertTrue(any("登录提示" in item for item in report["signals"]["login_required"]))
        self.assertEqual(report["assessment"]["classification"], "login_required")

    def test_captcha_detected(self):
        html = make_html("验证", extra='<img id="captcha" src="/captcha.png">')
        pages = {BASE + "/": html_response(html), LIST: html_response(html)}
        report = run(pages)
        self.assertTrue(report["signals"]["captcha"])
        self.assertEqual(report["assessment"]["classification"], "manual_review")

    def test_signature_detected_from_links_and_script(self):
        html = make_html("接口", [
            ("/api/items?sign=abc&timestamp=12", "数据"),
        ]) + "<script>var cfg={'signature': 'x'};</script>"
        pages = {BASE + "/": html_response(html), LIST: html_response(html)}
        report = run(pages)
        self.assertTrue(any("签名" in item for item in report["signals"]["signature"]))
        self.assertEqual(report["assessment"]["classification"], "manual_review")

    def test_timeout_recorded_and_scored(self):
        pages = {
            BASE + "/": html_response(make_html("首页")),
            LIST: timeout_response(LIST),
        }
        report = run(pages)
        list_page = next(page for page in report["pages"] if page["url"] == LIST)
        self.assertTrue(list_page["timed_out"])
        self.assertEqual(list_page["status"], 0)
        self.assertTrue(any(item["reason"] == "请求超时" for item in report["assessment"]["deductions"]))

    def test_redirect_chain_recorded(self):
        chain = [
            {"status": 301, "url": BASE + "/old"},
            {"status": 302, "url": BASE + "/new"},
        ]
        pages = {
            BASE + "/": html_response(make_html("首页"), final_url=BASE + "/new", redirects=chain),
            LIST: html_response(make_html("列表")),
        }
        report = run(pages)
        home = next(page for page in report["pages"] if page["url"] == BASE + "/")
        self.assertEqual(home["redirect_count"], 2)
        self.assertEqual(home["redirects"], chain)

    def test_accessible_site_scores_high(self):
        html = make_html("列表", [
            ("/list?page=2", "2"),
            ("/article/9.html", "详情"),
        ])
        pages = {BASE + "/": html_response(make_html("首页")), LIST: html_response(html)}
        report = run(pages)
        self.assertEqual(report["assessment"]["classification"], "accessible")
        self.assertGreaterEqual(report["assessment"]["score"], 80)
        self.assertIn(report["assessment"]["confidence_label"], ("high", "medium"))


if __name__ == "__main__":
    unittest.main()
