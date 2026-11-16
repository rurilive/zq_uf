"""可观测的回连接收端。

接收端负责记录所有回连：唯一标识、回连时间、来源地址和请求头。
来源地址与请求头用于判断回连是目标主机直接发出，还是经中间代理转发。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from .errors import ReceiverUnavailable

# 出现这些请求头时，回连很可能经过了代理/网关，而不是目标主机直连。
PROXY_HEADERS = (
    "via",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    "forwarded-for",
    "x-proxy-id",
    "x-bluecoat-via",
    "x-cache",
)


def analyze_headers(headers: Optional[Dict[str, str]]) -> Dict[str, object]:
    """根据请求头判断回连的转发路径。"""
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    marked = [name for name in PROXY_HEADERS if name in lowered]
    client = lowered.get("x-forwarded-for") or lowered.get("x-real-ip")
    if client:
        client = client.split(",")[0].strip() or None
    return {
        "forwarded": bool(marked),
        "via": "proxy" if marked else "direct",
        "client_address": client,
        "proxy_headers": marked,
    }


@dataclass
class Callback:
    """一条回连记录。"""

    token: str
    at: float
    source_address: str
    headers: Dict[str, str] = field(default_factory=dict)
    path: str = "/"
    method: str = "GET"

    def to_dict(self) -> Dict[str, object]:
        info = analyze_headers(self.headers)
        return {
            "id": self.token,
            "token": self.token,
            "at": self.at,
            "source_address": self.source_address,
            "client_address": info["client_address"] or self.source_address,
            "forwarded": info["forwarded"],
            "via": info["via"],
            "proxy_headers": info["proxy_headers"],
            "method": self.method,
            "path": self.path,
            "headers": dict(self.headers),
        }


class BaseReceiver:
    """接收端接口。

    自定义接收端只需实现 ``available()``、``callbacks()`` 和 ``base_url``；
    默认的 ``wait()`` 会轮询 ``callbacks()`` 直到超时。
    """

    base_url = ""

    def available(self) -> bool:  # pragma: no cover - 接口
        raise NotImplementedError

    def callbacks(self) -> List[Callback]:  # pragma: no cover - 接口
        raise NotImplementedError

    def wait(self, token: str, timeout: float, interval: float = 0.05) -> Optional[Callback]:
        """等待指定标识的回连；超时返回 None。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            for callback in self.callbacks():
                if callback.token == token:
                    return callback
            if time.monotonic() >= deadline:
                return None
            time.sleep(interval)


def _extract_token(path: str) -> Optional[str]:
    """从回连请求路径中取出唯一标识，如 ``/c/<token>`` 或 ``?t=<token>``。"""
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    for key in ("t", "token", "id", "sid"):
        if query.get(key):
            return query[key][0]
    segments = [seg for seg in parts.path.split("/") if seg]
    if not segments:
        return None
    if segments[0] in ("c", "cb", "callback") and len(segments) > 1:
        return segments[1]
    return segments[-1]


class EmbeddedReceiver(BaseReceiver):
    """内嵌的 HTTP 接收端，便于本地或自建环境直接使用。

    对端必须能访问到本机地址；跨网段时请传 ``public_base_url`` 指到可达地址。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0, public_base_url: Optional[str] = None):
        self._host = host
        self._port = port
        self._public_base_url = public_base_url
        self._records: List[Callback] = []
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> "EmbeddedReceiver":
        if self._server is not None:
            return self
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                receiver._record(self)

            def do_POST(self):  # noqa: N802
                receiver._record(self)

            def log_message(self, *args):  # 静默，避免污染调用方输出
                return

        try:
            server = ThreadingHTTPServer((self._host, self._port), Handler)
        except OSError as exc:  # 端口占用等
            raise ReceiverUnavailable(f"cannot start callback receiver: {exc}") from exc
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="ssrfverify-receiver", daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    # -- 观测 -------------------------------------------------------------
    def available(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def base_url(self) -> str:
        if self._public_base_url:
            return self._public_base_url
        if self._server is None:
            return ""
        host, port = self._server.server_address[:2]
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    def callbacks(self) -> List[Callback]:
        with self._lock:
            return list(self._records)

    def _record(self, handler: BaseHTTPRequestHandler) -> None:
        callback = Callback(
            token=_extract_token(handler.path) or "",
            at=time.time(),
            source_address=handler.client_address[0],
            headers={str(k): str(v) for k, v in handler.headers.items()},
            path=handler.path,
            method=handler.command,
        )
        with self._lock:
            self._records.append(callback)
        handler.send_response(204)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
