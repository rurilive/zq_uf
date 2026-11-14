"""预检 I/O 层。

所有真实网络操作都集中在 :class:`StdlibGateway`，仅使用 Python 标准库。
预检引擎只依赖本模块定义的三个方法（鸭子类型），测试时用 FakeGateway 替换，
保证测试过程不发出任何真实请求。
"""

from __future__ import annotations

import re
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

USER_AGENT = "crawlprobe/1.0 (+pre-flight probe)"


@dataclass(frozen=True)
class CertInfo:
    """TLS 证书摘要。"""

    subject: str
    issuer: str
    not_before: datetime | None
    not_after: datetime | None


@dataclass(frozen=True)
class HTTPResponse:
    """一次 HTTP GET 的结果（4xx/5xx 也正常返回，不抛异常）。"""

    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        """按 Content-Type 声明的字符集解码，缺省按 UTF-8 容错解码。"""
        match = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""))
        encoding = match.group(1) if match else "utf-8"
        return self.body.decode(encoding, errors="replace")


def _lower_headers(headers) -> dict[str, str]:
    return {key.lower(): value for key, value in dict(headers).items()}


def _cert_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # Python 3.13 起 cert_time_to_seconds 被移除，保留纯标准库回退解析。
    if hasattr(ssl, "cert_time_to_seconds"):
        timestamp = ssl.cert_time_to_seconds(value)
    else:
        timestamp = datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc).timestamp()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _cert_name(cert: dict, key: str) -> str:
    for rdn in cert.get(key, ()):
        for attr, value in rdn:
            if attr == "commonName":
                return value
    return ""


class StdlibGateway:
    """基于 socket / ssl / urllib 的默认 I/O 实现。"""

    def __init__(self, *, timeout: float = 8.0, user_agent: str = USER_AGENT):
        self.timeout = timeout
        self.user_agent = user_agent

    def resolve(self, host: str) -> list[str]:
        """DNS 解析，返回去重后的 IP 列表；失败时抛出 socket.gaierror/OSError。"""
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        seen: list[str] = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen:
                seen.append(ip)
        return seen

    def tls_probe(self, host: str, port: int = 443) -> CertInfo:
        """建立 TLS 握手并返回证书摘要。

        证书过期、自签名、主机名不匹配等会抛出 ``ssl.SSLError``；
        网络不可达等抛出 ``OSError``，由调用方归类为检查失败。
        """
        with socket.create_connection((host, port), timeout=self.timeout) as raw:
            context = ssl.create_default_context()
            with context.wrap_socket(raw, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
        return CertInfo(
            subject=_cert_name(cert, "subject"),
            issuer=_cert_name(cert, "issuer"),
            not_before=_cert_time(cert.get("notBefore")),
            not_after=_cert_time(cert.get("notAfter")),
        )

    def http_get(self, url: str) -> HTTPResponse:
        """发起 GET。

        4xx/5xx 封装成 :class:`HTTPResponse` 返回；DNS/连接类错误包装为
        ``urllib.error.URLError``（或 OSError）抛出，由调用方处理。
        """
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return HTTPResponse(
                    url=response.geturl(),
                    status=response.status,
                    headers=_lower_headers(response.headers),
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except OSError:
                body = b""
            return HTTPResponse(
                url=exc.geturl() or url,
                status=exc.code,
                headers=_lower_headers(exc.headers or {}),
                body=body,
            )
