import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ssrfverify import check, report, reset, start_receiver


class DemoTarget(BaseHTTPRequestHandler):
    """模拟存在 SSRF 的目标：把 id 当 URL 拉取。"""

    def log_message(self, *args):
        pass

    def do_GET(self):
        callback = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
        if callback.startswith("http"):
            try:
                urllib.request.urlopen(callback, timeout=2)
            except Exception:
                pass
        body = b"item detail"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


target = ThreadingHTTPServer(("127.0.0.1", 0), DemoTarget)
threading.Thread(target=target.serve_forever, daemon=True).start()
base = "http://127.0.0.1:{}/item?id=1".format(target.server_address[1])

receiver = start_receiver()
reset()

print(check(base, "id"))
print(check(base, "id"))  # 同一参数重复试，报告里只算一条
print(report())
print("receiver records:", receiver.records())
