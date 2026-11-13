"""本地起一个演示服务器，跑三类站点并展示报告落盘与改版对比。"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from siteprobe import Samples, diff_reports, probe, save_report

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def page(title, anchors=(), extra="", encoding="utf-8"):
    body = "<html><head><meta charset='%s'><title>%s</title></head><body><h1>%s</h1>" % (
        encoding, title, title)
    for href, text in anchors:
        body += '<a href="%s">%s</a>' % (href, text)
    body += extra + "</body></html>"
    return body.encode(encoding)


class DemoHandler(BaseHTTPRequestHandler):
    revision = 1

    def log_message(self, fmt, *args):
        return

    def _send(self, body, status=200, content_type="text/html; charset=utf-8", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/accessible"):
            return self._accessible(path, query)
        if path.startswith("/login"):
            return self._login_site(path)
        if path.startswith("/manual"):
            return self._manual_site(path)
        return self._send(b"not found", status=404)

    def _accessible(self, path, query):
        if path in ("/accessible", "/accessible/"):
            body = page("可接入站点-首页", [("/accessible/list", "新闻列表")])
            return self._send(body)
        if path == "/accessible/list":
            page_no = int(query.get("page", ["1"])[0])
            anchors = [("/accessible/list?page=%d" % index, str(index)) for index in (2, 3, 4)]
            anchors.append(("/accessible/list?page=%d" % (page_no + 1), "下一页"))
            anchors += [("/accessible/news/%d.html" % index, "新闻%d" % index) for index in range(101, 105)]
            return self._send(page("新闻列表", anchors))
        if path.startswith("/accessible/news/"):
            return self._send(page("新闻详情", [("/accessible/list", "返回列表")]))
        return self._send(b"not found", status=404)

    def _login_site(self, path):
        if path in ("/login", "/login/"):
            return self._send(page("登录拦截站-首页", [("/login/list", "内容列表")]))
        if path == "/login/list":
            self.send_response(302)
            self.send_header("Location", "/login/signin")
            self.end_headers()
            return
        if path == "/login/signin":
            body = page("用户登录", [("/login/signin", "立即登录")],
                        extra="<p>请先登录后再访问内容</p>")
            return self._send(body)
        return self._send(b"not found", status=404)

    def _manual_site(self, path):
        if path in ("/manual", "/manual/"):
            return self._send(page("风控站点-首页", [("/manual/list", "数据列表")]))
        if path == "/manual/list":
            extra = '<img id="captcha" src="/manual/captcha.png">'
            anchors = [("/manual/api/items?sign=abc&amp;timestamp=1700000000", "接口数据")]
            if self.revision == 2:
                extra += "<p>当前需要完成滑块验证</p>"
            body = page("风控列表", anchors, extra=extra)
            return self._send(body)
        if path.startswith("/manual/api/"):
            payload = b'{"code": 0, "data": []}'
            return self._send(payload, content_type="application/json; charset=utf-8")
        if path == "/manual/captcha.png":
            return self._send(b"PNG-PLACEHOLDER", content_type="image/png")
        return self._send(b"not found", status=404)


def run_demo():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % port
    plans = [
        ("可接入站点", base + "/accessible", [base + "/accessible/list"]),
        ("需登录站点", base + "/login", [base + "/login/list"]),
        ("需人工站点", base + "/manual", [base + "/manual/list"]),
    ]
    first_reports = {}
    try:
        for name, home, list_urls in plans:
            report = probe(home, Samples(list_urls=list_urls, rate_per_sec=8, max_workers=4, timeout=5))
            first_reports[name] = report
            save_report(report, os.path.join(REPORT_DIR, name + ".json"))
            assessment = report["assessment"]
            print("\n== %s ==" % name)
            print("分类: %s | 分数: %d | 置信度: %s(%.2f)" % (
                assessment["classification"], assessment["score"],
                assessment["confidence_label"], assessment["confidence"]))
            print("分页: style=%s param=%s total=%s selector=%s" % (
                report["pagination"]["style"], report["pagination"]["page_param"],
                report["pagination"]["total_pages"], report["pagination"]["next_selector"]))
            print("详情: pattern=%s 样本=%s" % (
                report["details"]["detail_pattern"], report["details"]["urls"]))
            print("信号:", json.dumps(report["signals"], ensure_ascii=False))

        DemoHandler.revision = 2
        changed = probe(base + "/manual", Samples(
            list_urls=[base + "/manual/list"], rate_per_sec=8, timeout=5))
        diff = diff_reports(first_reports["需人工站点"], changed)
        print("\n== 改版对比（需人工站点 revision 1 -> 2）==")
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        print("\n报告已写入: %s" % REPORT_DIR)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run_demo()
