"""本地服务模拟 304：整页下载 -> 新鲜期命中缓存 -> 过期先回旧值、后台条件刷新。"""

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from condfetch import fetch

PAGE = {"body": "detail-v1", "etag": '"v1"'}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("If-None-Match") == PAGE["etag"]:
            self.send_response(304)
            self.send_header("ETag", PAGE["etag"])
            self.send_header("Cache-Control", "max-age=1")
            self.end_headers()
            return
        body = PAGE["body"].encode("utf-8")
        self.send_response(200)
        self.send_header("ETag", PAGE["etag"])
        self.send_header("Cache-Control", "max-age=1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/page" % server.server_port

print(fetch(url))  # 200，整页下载并写入共享缓存
print(fetch(url))  # 新鲜期内，直接命中缓存，不请求
time.sleep(1.2)
print(fetch(url))  # 已过期：先返回旧值，后台带 If-None-Match 刷新（304）
time.sleep(0.3)    # 等后台刷新落地
print(fetch(url))  # 后台 304 已续期，再次命中缓存
server.shutdown()
