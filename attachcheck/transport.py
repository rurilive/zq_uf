"""最小 HTTP 传输层：只依赖标准库，支持 Range / If-Range。"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request


class TransportError(Exception):
    """连接层面的失败（没有拿到 HTTP 状态码时使用）。"""


class Response:
    """把 urllib 的响应收敛成 fetch 需要的最小接口。"""

    def __init__(self, url, status, headers, raw):
        self.url = url
        self.status = status
        # 统一小写，后续按头名字符串取值不区分大小写。
        self.headers = {str(k).lower(): str(v) for k, v in headers.items()}
        self.raw = raw

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, size):
        return self.raw.read(size)

    def close(self):
        try:
            self.raw.close()
        except Exception:  # 已断开时关闭也会抛，忽略即可
            pass


class UrllibTransport:
    """用 urllib 发 GET；offset > 0 时带 Range 与 If-Range。"""

    def open(self, url, *, offset=0, etag=None, last_modified=None, timeout=10.0):
        headers = {
            "User-Agent": "attachcheck/1.0",
            "Accept-Encoding": "identity",
        }
        if offset:
            headers["Range"] = f"bytes={offset}-"
            validator = etag or last_modified
            if validator:
                headers["If-Range"] = validator

        request = urllib.request.Request(url, headers=headers)
        try:
            raw = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 也是有效响应：保留状态码交给上层记录。
            return Response(url, exc.code, exc.headers, exc)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise TransportError(str(exc)) from exc
        return Response(url, raw.status, raw.headers, raw)
