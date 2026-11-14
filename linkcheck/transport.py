"""默认 HTTP 传输层。

纯标准库实现：urllib + 自定义重定向处理器，禁止自动跟随 3xx，
把每一跳响应原样交给巡检引擎，由引擎记录完整重定向链、检测环。
传输层只负责发请求和给异常分类，不做重试（重试/退避在引擎里）。
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error
import urllib.request

# 失败分类常量；引擎按这些类别决定是否退避重试。
CAT_TIMEOUT = "timeout"
CAT_CONNECTION = "connection"
CAT_DNS = "dns"
CAT_SSL = "ssl"
CAT_INVALID = "invalid"

# 仅超时和连接类错误值得重试；DNS/SSL/参数错误重试无意义。
RETRYABLE_CATEGORIES = frozenset({CAT_TIMEOUT, CAT_CONNECTION})


class FetchError(Exception):
    """请求失败，category 取上面的 CAT_* 常量。"""

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category
        self.message = message


class FetchResponse:
    """一跳的响应：状态码、响应头、实际 URL。"""

    __slots__ = ("status", "headers", "url")

    def __init__(self, status, headers, url):
        self.status = status
        self.headers = headers
        self.url = url

    def location(self):
        """大小写不敏感地取 Location 头。"""
        for key, value in self.headers.items():
            if key.lower() == "location":
                return value
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """让 urllib 不要自动跟随重定向，3xx 响应直接返回给调用方。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    # redirect_request 返回 None 时基类会抛 HTTPError，
    # 这里统一把响应放行，保证 3xx 像普通响应一样回到 request()。
    def http_error_301(self, req, fp, code, msg, headers):
        return urllib.response.addinfourl(fp, headers, req.full_url, code)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


class UrllibTransport:
    """线上默认传输；测试可替换成同接口的假传输。"""

    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, url, method="HEAD"):
        """发一次请求，返回 FetchResponse；失败抛 FetchError。"""
        req = urllib.request.Request(url, method=method)
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            # 4xx/5xx（以及被拦截的 3xx，理论上走不到这里）都算正常响应，
            # 由引擎按状态码分类。
            headers = dict(exc.headers.items()) if exc.headers else {}
            return FetchResponse(exc.code, headers, exc.url or url)
        except urllib.error.URLError as exc:
            raise self._classify(exc.reason) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise FetchError(CAT_TIMEOUT, str(exc)) from exc
        except (http.client.HTTPException, ValueError, UnicodeError) as exc:
            # 非法 URL、端口、URL 编码等属于输入问题。
            raise FetchError(CAT_INVALID, str(exc)) from exc
        return FetchResponse(resp.status, dict(resp.headers.items()), resp.url)

    @staticmethod
    def _classify(reason):
        if isinstance(reason, (TimeoutError, socket.timeout)):
            category = CAT_TIMEOUT
        elif isinstance(reason, socket.gaierror):
            category = CAT_DNS
        elif isinstance(reason, ssl.SSLError):
            category = CAT_SSL
        elif isinstance(reason, ConnectionError):
            category = CAT_CONNECTION
        elif isinstance(reason, (ValueError, UnicodeError)):
            category = CAT_INVALID
        else:
            category = CAT_CONNECTION
        return FetchError(category, f"{type(reason).__name__}: {reason}")
