"""HTTPS 请求：区分证书错误与网络错误。

证书错误（过期、主机名不匹配、链不完整）不重试，抛出 CertError，
携带站点、证书主题和到期时间；临时放行名单内的站点可跳过校验，
每次放行写审计日志。网络等其他错误按 retries 重试后抛 FetchError。
"""

import json
import logging
import socket
import ssl
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

logger = logging.getLogger("tlssafe")
audit_logger = logging.getLogger("tlssafe.audit")

CERT_EXPIRED = "expired"
CERT_HOSTNAME_MISMATCH = "hostname_mismatch"
CERT_INCOMPLETE_CHAIN = "incomplete_chain"
CERT_UNKNOWN = "unknown_cert"

DEFAULT_RETRIES = 1
CERT_INFO_TIMEOUT = 5  # 抓取对端证书详情的超时（秒）
ALLOWLIST_DEFAULT_MINUTES = 60  # 放行名单默认有效期（分钟）

_opener = None
_unverified_opener = None
_ca_file = None
_allowlist = {}  # host -> {"until": datetime, "reason": str}
_audit_file = None


def set_opener(func):
    """设置正常（校验证书）的请求函数，签名为 func(url)。"""
    global _opener
    _opener = func


def set_unverified_opener(func):
    """设置放行时使用的免校验请求函数，默认用 urllib + 不校验上下文。"""
    global _unverified_opener
    _unverified_opener = func


def set_ca_file(path):
    """设置默认 opener 使用的 CA bundle 路径。"""
    global _ca_file
    _ca_file = path


def set_audit_log(path):
    """设置审计日志文件（JSONL），None 表示只走 logging。"""
    global _audit_file
    _audit_file = path


class FetchError(Exception):
    def __init__(self, url, reason):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class CertError(FetchError):
    """证书校验失败。kind 见 CERT_* 常量，action 为处理结果。"""

    def __init__(self, url, reason, kind, cert_subject="unknown",
                 cert_expires="unknown", action="rejected"):
        super().__init__(url, reason)
        self.kind = kind
        self.cert_subject = cert_subject
        self.cert_expires = cert_expires
        self.action = action

    def __str__(self):
        return (f"{self.url}: [{self.kind}] {self.reason} "
                f"(subject={self.cert_subject}, expires={self.cert_expires}, "
                f"action={self.action})")


def allow_host(host, minutes=ALLOWLIST_DEFAULT_MINUTES, reason=""):
    """把 host 加入临时放行名单，返回过期时间（UTC）。"""
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    _allowlist[host] = {"until": until, "reason": reason}
    logger.info("放行名单新增 %s，有效期 %d 分钟，原因: %s", host, minutes, reason)
    return until


def revoke_host(host):
    _allowlist.pop(host, None)


def clear_allowlist():
    _allowlist.clear()


def _allowlist_entry(host):
    entry = _allowlist.get(host)
    if entry is None:
        return None
    if entry["until"] <= datetime.now(timezone.utc):
        del _allowlist[host]
        logger.warning("放行名单条目已过期，自动移除: %s", host)
        return None
    return entry


def _audit(event):
    record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    line = json.dumps(record, ensure_ascii=False)
    audit_logger.info("audit %s", line)
    if _audit_file:
        with open(_audit_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _classify(exc):
    code = getattr(exc, "verify_code", None)
    msg = str(getattr(exc, "verify_message", "") or exc).lower()
    if code == 10 or "expired" in msg:
        return CERT_EXPIRED
    if code == 62 or "hostname" in msg or "doesn't match" in msg \
            or "ip address mismatch" in msg:
        return CERT_HOSTNAME_MISMATCH
    if code in (20, 21) or "unable to get local issuer" in msg \
            or "unable to verify the first certificate" in msg:
        return CERT_INCOMPLETE_CHAIN
    return CERT_UNKNOWN


def _format_subject(subject):
    parts = []
    for rdn in subject:
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def _decode_cert_der(der):
    pem = ssl.DER_cert_to_PEM_cert(der)
    decode = getattr(ssl._ssl, "_test_decode_cert", None)  # 标准库内部解码器
    if decode is None:
        return None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as fh:
            fh.write(pem)
            fh.flush()
            decoded = decode(fh.name)
    except Exception as exc:
        logger.warning("解析证书内容失败: %s", exc)
        return None
    subject = _format_subject(decoded.get("subject", ())) or "unknown"
    not_after = decoded.get("notAfter")
    if not_after:
        expires = datetime.fromtimestamp(
            ssl.cert_time_to_seconds(not_after), timezone.utc).isoformat()
    else:
        expires = "unknown"
    return subject, expires


def _peer_cert_info(url):
    """用免校验连接抓取对端证书的主题和到期时间，失败返回 None。"""
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or 443
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=CERT_INFO_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der = ssock.getpeercert(binary_form=True)
    except OSError as exc:
        logger.warning("抓取 %s 的证书失败: %s", url, exc)
        return None
    if not der:
        return None
    return _decode_cert_der(der)


def _cert_info(url, exc):
    subject = getattr(exc, "cert_subject", None)
    expires = getattr(exc, "cert_expires", None)
    if subject and expires:
        return subject, expires
    info = _peer_cert_info(url)
    if info:
        return info
    logger.warning("无法获取 %s 的证书详情，subject/expires 记为 unknown", url)
    return "unknown", "unknown"


def _verified_context():
    if _ca_file:
        return ssl.create_default_context(cafile=_ca_file)
    return ssl.create_default_context()


def _default_open(url):
    return urllib.request.urlopen(url, context=_verified_context())


def _default_open_unverified(url):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.urlopen(url, context=ctx)


def _open(url):
    return (_opener or _default_open)(url)


def _open_unverified(url):
    return (_unverified_opener or _default_open_unverified)(url)


def _unwrap_cert_error(exc):
    """从 URLError 等包装异常里找出 SSLCertVerificationError。"""
    seen = set()
    queue = [exc, getattr(exc, "reason", None),
             getattr(exc, "__cause__", None), getattr(exc, "__context__", None)]
    while queue:
        current = queue.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        queue.extend([getattr(current, "reason", None),
                      getattr(current, "__cause__", None),
                      getattr(current, "__context__", None)])
    return None


def _handle_cert_error(url, exc):
    host = urlsplit(url).hostname or ""
    kind = _classify(exc)
    subject, expires = _cert_info(url, exc)
    reason = str(getattr(exc, "verify_message", "") or exc)
    entry = _allowlist_entry(host)
    if entry is not None:
        event = {
            "event": "allowlist_bypass",
            "url": url,
            "host": host,
            "kind": kind,
            "cert_subject": subject,
            "cert_expires": expires,
            "allowlist_reason": entry["reason"],
            "allowlist_until": entry["until"].isoformat(),
        }
        try:
            result = _open_unverified(url)
        except Exception as open_exc:
            _audit({**event, "result": "bypass_failed", "error": str(open_exc)})
            raise CertError(url, reason, kind, subject, expires,
                            action="bypass_failed") from open_exc
        _audit({**event, "result": "bypassed"})
        logger.warning("已按放行名单跳过证书校验: %s (%s)", url, kind)
        return result
    logger.error("证书错误已拒绝: %s kind=%s subject=%s expires=%s",
                 url, kind, subject, expires)
    raise CertError(url, reason, kind, subject, expires,
                    action="rejected") from exc


def fetch(url, retries=DEFAULT_RETRIES):
    """请求 url。证书错误不重试；其他错误重试 retries 次后抛 FetchError。"""
    last = None
    for _ in range(retries + 1):
        try:
            return _open(url)
        except ssl.SSLCertVerificationError as exc:
            return _handle_cert_error(url, exc)
        except Exception as exc:
            cert_exc = _unwrap_cert_error(exc)
            if cert_exc is not None:
                return _handle_cert_error(url, cert_exc)
            last = exc
            logger.warning("请求失败（非证书错误），准备重试: %s: %s", url, exc)
    raise FetchError(url, str(last))
