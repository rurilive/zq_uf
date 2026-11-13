"""基于 urllib 的默认抓取器，记录超时与完整重定向链。"""

import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin


class HttpFetcher:
    """真实 HTTP 抓取器，返回统一字典结构，供 probe 消费。"""

    def __init__(self, timeout=10.0, max_redirects=10, user_agent="siteprobe/1.0"):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    def __call__(self, url, timeout=None):
        chain = []
        limit = self.max_redirects

        class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                absolute = urljoin(req.full_url, newurl)
                chain.append({"status": code, "url": absolute})
                if len(chain) >= limit:
                    raise urllib.error.HTTPError(req.full_url, code, "too many redirects", headers, fp)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(_RedirectRecorder())
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        started = time.monotonic()
        try:
            response = opener.open(request, timeout=timeout or self.timeout)
            body = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
            return {
                "status": response.status,
                "headers": headers,
                "body": body,
                "url": response.geturl(),
                "redirects": list(chain),
                "elapsed": time.monotonic() - started,
            }
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return {
                "status": exc.code,
                "headers": {key.lower(): value for key, value in (exc.headers or {}).items()},
                "body": body,
                "url": url,
                "redirects": list(chain),
                "elapsed": time.monotonic() - started,
                "error": "http_%s" % exc.code,
            }
        except Exception as exc:  # 超时、连接失败等统一落盘，不让探测中断
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            timed_out = isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower()
            return {
                "status": 0,
                "headers": {},
                "body": b"",
                "url": url,
                "redirects": list(chain),
                "elapsed": time.monotonic() - started,
                "error": "timeout" if timed_out else "connection_error: %s" % reason,
            }
