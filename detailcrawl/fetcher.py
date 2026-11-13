"""按域名复用连接的 HTTP 抓取，错误按类型分类。"""
from __future__ import annotations

import http.client
import threading
from urllib.parse import urlsplit

_DEFAULT_TIMEOUT = 10.0
_USER_AGENT = "detailcrawl/1.0"


class FetchError(Exception):
    """抓取失败基类，retryable 标记是否值得重试。"""

    retryable = False

    def __init__(self, url, reason):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class TimeoutFetchError(FetchError):
    retryable = True


class HttpStatusError(FetchError):
    def __init__(self, url, status):
        super().__init__(url, f"HTTP {status}")
        self.status = status
        self.retryable = 500 <= status < 600


def _endpoint(url):
    parts = urlsplit(url)
    scheme = parts.scheme or "http"
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port


class ConnectionPool:
    """按 (协议, 域名, 端口) 复用 keep-alive 连接。"""

    def __init__(self, timeout=_DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._idle = {}
        self._lock = threading.Lock()
        self._opened = 0

    def connection(self, url):
        key = _endpoint(url)
        with self._lock:
            idle = self._idle.get(key)
            conn = idle.pop() if idle else None
            if conn is None:
                self._opened += 1
        if conn is None:
            scheme, host, port = key
            cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            conn = cls(host, port, timeout=self._timeout)
        return key, conn

    def release(self, key, conn, reusable):
        if not reusable:
            conn.close()
            return
        with self._lock:
            self._idle.setdefault(key, []).append(conn)

    @property
    def opened_count(self):
        with self._lock:
            return self._opened

    def close(self):
        with self._lock:
            idle = [conn for conns in self._idle.values() for conn in conns]
            self._idle.clear()
        for conn in idle:
            conn.close()


class HttpFetcher:
    """抓取 URL 返回文本，失败抛出分类后的 FetchError。"""

    def __init__(self, timeout=_DEFAULT_TIMEOUT, pool=None):
        self.pool = pool if pool is not None else ConnectionPool(timeout)
        self._owns_pool = pool is None

    def __call__(self, url):
        return self.fetch(url)

    def fetch(self, url):
        try:
            return self._fetch_once(url)
        except http.client.RemoteDisconnected:
            # 服务端关掉了 keep-alive 连接，换新连接补一次
            try:
                return self._fetch_once(url)
            except http.client.RemoteDisconnected as exc:
                raise FetchError(url, "connection dropped") from exc

    def _fetch_once(self, url):
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        key, conn = self.pool.connection(url)
        reusable = False
        try:
            conn.request("GET", path, headers={"User-Agent": _USER_AGENT})
            response = conn.getresponse()
            body = response.read()
            reusable = True
        except TimeoutError as exc:
            raise TimeoutFetchError(url, "timed out") from exc
        except http.client.RemoteDisconnected:
            raise
        except (http.client.HTTPException, OSError) as exc:
            raise FetchError(url, str(exc) or type(exc).__name__) from exc
        finally:
            self.pool.release(key, conn, reusable)
        if response.status != 200:
            raise HttpStatusError(url, response.status)
        return body.decode("utf-8", errors="replace")

    def close(self):
        if self._owns_pool:
            self.pool.close()
