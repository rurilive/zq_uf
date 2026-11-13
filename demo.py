"""本地 HTTP 服务演示：断连保留 .part，重试按字节续传并校验。"""

import hashlib
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from filefetch import VerificationError, download


PAYLOAD = bytes(range(256)) * 64


class FlakyHandler(BaseHTTPRequestHandler):
    flaky_offsets = {0}

    def log_message(self, format, *args):
        pass

    def _send(self, start, flaky):
        body = PAYLOAD[start:start + 3] if flaky else PAYLOAD[start:]
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        declared_length = len(PAYLOAD) - start
        self.send_header("Content-Length", str(declared_length))
        self.send_header("Content-Range", "bytes %d-%d/%d"
                         % (start, len(PAYLOAD) - 1, len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(body)
        if flaky:
            self.wfile.flush()
            self.close_connection = True

    def do_GET(self):
        range_value = self.headers.get("Range")
        if range_value == "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Length", "1")
            self.send_header("Content-Range", "bytes 0-0/%d" % len(PAYLOAD))
            self.end_headers()
            self.wfile.write(PAYLOAD[:1])
            return

        start = (int(range_value.split("=", 1)[1].split("-", 1)[0])
                 if range_value else 0)
        flaky = start in self.flaky_offsets
        if flaky:
            self.flaky_offsets.discard(start)
        self._send(start, flaky)


def main():
    server = HTTPServer(("127.0.0.1", 0), FlakyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/report.bin" % server.server_port
    digest = hashlib.sha256(PAYLOAD).hexdigest()

    with tempfile.TemporaryDirectory() as tmp:
        print("目标目录:", tmp)
        try:
            download(url, tmp, checksum=("sha256", digest))
        except VerificationError as exc:
            parts = [name for name in os.listdir(tmp) if name.endswith(".part")]
            print("首次下载断连:", exc, "\n保留临时文件:", parts)

        size = download(url, tmp, checksum=("sha256", digest))
        name = next(name for name in os.listdir(tmp) if not name.endswith(".part"))
        print("续传完成:", name, size, "字节，校验通过")

    server.shutdown()


if __name__ == "__main__":
    main()
