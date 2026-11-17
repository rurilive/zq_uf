import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ssrfverify import (
    ReceiverUnavailable,
    check,
    report,
    reset,
    start_receiver,
    stop_receiver,
)


def make_target():
    """模拟目标：/item 会把 id 当 URL 拉取（SSRF 行为），/broken 直接 500。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/broken":
                body = b"internal stack trace: db connection refused"
                self.send_response(500)
            elif path == "/item":
                callback = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                if callback.startswith("http"):
                    try:
                        urllib.request.urlopen(callback, timeout=2)
                    except Exception:
                        pass
                body = b"item detail"
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = make_target()
        cls.base = "http://127.0.0.1:{}".format(cls.target.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.target.shutdown()

    def setUp(self):
        stop_receiver()
        self.receiver = start_receiver()
        reset()

    def url(self, path):
        return self.base + path

    def test_callback_means_hit(self):
        got = check(self.url("/item?id=1"), "id")
        self.assertTrue(got["hit"])
        self.assertIsNotNone(got["callback_at"])
        self.assertEqual(got["source"], "127.0.0.1")
        self.assertTrue(got["direct"])
        self.assertEqual(got["headers"].get("Host"),
                         self.receiver.base_url.split("://")[1])

    def test_no_callback_normal_response_is_clean(self):
        got = check(self.url("/item?id=1"), "other")
        self.assertFalse(got["hit"])
        self.assertFalse(got["suspicious"])

    def test_no_callback_but_5xx_is_suspicious(self):
        got = check(self.url("/broken"), "id")
        self.assertFalse(got["hit"])
        self.assertTrue(got["suspicious"])
        self.assertIn("500", got["reason"])
        self.assertIn("stack trace", got["response"])

    def test_report_dedups_and_matches_callbacks(self):
        url = self.url("/item?id=9")
        check(url, "id")
        check(url, "id")
        check(self.url("/broken"), "id")
        result = report()
        self.assertEqual(result["checks"], 3)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(len(result["hits"]), 1)
        self.assertEqual(len(result["callbacks"]), 1)
        self.assertEqual(result["hits"][0]["token"],
                         result["callbacks"][0]["token"])
        receiver_tokens = {r["token"] for r in self.receiver.records()}
        self.assertEqual(len(receiver_tokens), 2)  # 接收端保留全部原始回连
        for c in result["callbacks"]:
            self.assertIn(c["token"], receiver_tokens)  # 报告逐条可对上
        self.assertIn(self.url("/broken"), result["suspicious"])

    def test_suspicious_grouped_by_endpoint(self):
        check(self.url("/broken"), "id")
        check(self.url("/broken"), "uid")
        result = report()
        self.assertEqual(len(result["suspicious"][self.url("/broken")]), 2)

    def test_receiver_down_stops_round(self):
        stop_receiver()
        with self.assertRaises(ReceiverUnavailable):
            check(self.url("/item?id=1"), "id")
        with self.assertRaises(ReceiverUnavailable):
            check(self.url("/item?id=2"), "id")
        self.assertTrue(report()["aborted"])
        reset()
        self.assertFalse(report()["aborted"])

    def test_param_required(self):
        with self.assertRaises(ValueError):
            check(self.url("/item"), "")


if __name__ == "__main__":
    unittest.main()
