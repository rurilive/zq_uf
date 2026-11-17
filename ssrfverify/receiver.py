"""回连接收端：记录每一次回连的唯一标识、来源地址和请求头。"""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CallbackReceiver:
    """可观测的回连接收端，命中以这里的记录为准。"""

    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self._records = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    @property
    def base_url(self):
        return "http://{}:{}".format(self.host, self.port)

    def start(self):
        if self._server is not None:
            return self
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                record = {
                    "token": self.path.rstrip("/").rsplit("/", 1)[-1],
                    "path": self.path,
                    "source": self.client_address[0],
                    "headers": dict(self.headers.items()),
                    "at": time.time(),
                }
                with receiver._lock:
                    receiver._records.append(record)
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _handle
            do_POST = _handle

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def alive(self):
        """接收端是否可达，不可达时检测方必须停止本轮。"""
        if self._server is None:
            return False
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except OSError:
            return False

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def records(self):
        with self._lock:
            return [dict(r, headers=dict(r["headers"])) for r in self._records]

    def find(self, token):
        with self._lock:
            for r in self._records:
                if r["token"] == token:
                    return dict(r, headers=dict(r["headers"]))
        return None

    def wait_for(self, token, timeout, interval=0.05):
        """在 timeout 秒内等待某个 token 的回连，拿到记录或超时返回 None。"""
        deadline = time.time() + timeout
        while True:
            record = self.find(token)
            if record is not None:
                return record
            if time.time() >= deadline:
                return None
            time.sleep(interval)

    def clear(self):
        with self._lock:
            self._records.clear()
