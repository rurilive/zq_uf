"""测试用假抓取器与 HTML 构造工具。"""

from urllib.parse import urlparse


def make_html(title="", anchors=(), extra=""):
    body = "<html><head><title>%s</title></head><body><h1>%s</h1>" % (title, title)
    for href, text in anchors:
        cls = ""
        if isinstance(href, tuple):
            href, cls = href
            cls = ' class="%s"' % cls
        body += '<a%s href="%s">%s</a>' % (cls, href, text)
    body += extra + "</body></html>"
    return body


class FakeFetcher:
    """按 URL 登记响应，支持超时、重定向和调用计数。"""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        response = self.pages.get(url)
        if response is None:
            return {"status": 0, "body": b"", "headers": {}, "url": url,
                    "redirects": [], "elapsed": 0.0, "error": "connection_error: not found"}
        return response


def html_response(body, content_type="text/html; charset=utf-8", status=200,
                  final_url=None, redirects=None, error="", elapsed=0.01):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return {
        "status": status,
        "body": body,
        "headers": {"content-type": content_type},
        "url": final_url,
        "redirects": redirects or [],
        "elapsed": elapsed,
        "error": error,
    }


def timeout_response(url):
    return {"status": 0, "body": b"", "headers": {}, "url": url,
            "redirects": [], "elapsed": 10.0, "error": "timeout"}


def same_host(url_a, url_b):
    return urlparse(url_a).netloc == urlparse(url_b).netloc
