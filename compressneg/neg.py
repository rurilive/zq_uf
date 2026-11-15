"""按站点协商压缩与编码：先探测、按站点记忆、失败回退 identity。"""

from __future__ import annotations

import codecs
import gzip
import re
import time
import zlib
from dataclasses import dataclass, field

try:
    import brotli
except ImportError:
    brotli = None

try:
    import zstandard
except ImportError:
    zstandard = None

# 压缩方式偏好顺序，identity 永远兜底。
PREFERENCE = ("br", "zstd", "gzip", "deflate", "identity")


class NegotiationError(Exception):
    """协商无法进行（例如没有可用的探测函数）。"""


@dataclass
class Record:
    """单个站点的协商记录，只保留最近一次结果。"""

    site: str
    accept_encoding: str = "identity"
    charset: str | None = None
    locked: bool = False          # 人工覆盖后锁定，自动协商不再改回
    source: str = "auto"          # auto=探测得出，manual=人工覆盖
    updated_at: float = field(default_factory=time.time)
    last_error: str | None = None


def _supported(kind):
    if kind == "br":
        return brotli is not None
    if kind == "zstd":
        return zstandard is not None
    return True


def _decompress(kind, body):
    """按 content-encoding 解压，不支持的编码抛 NegotiationError。"""
    if kind in ("identity", ""):
        return body
    if kind == "gzip":
        return gzip.decompress(body)
    if kind == "deflate":
        return zlib.decompress(body)
    if kind == "br":
        if brotli is None:
            raise NegotiationError("br 解压不可用（未安装 brotli）")
        return brotli.decompress(body)
    if kind == "zstd":
        if zstandard is None:
            raise NegotiationError("zstd 解压不可用（未安装 zstandard）")
        return zstandard.ZstdDecompressor().decompress(body)
    raise NegotiationError(f"未知 content-encoding: {kind}")


def _header(headers_, name):
    for key, value in (headers_ or {}).items():
        if key.lower() == name:
            return value
    return None


def _content_encoding(response):
    if "content_encoding" in response:
        return response["content_encoding"] or "identity"
    return _header(response.get("headers"), "content-encoding") or "identity"


def _declared_charset(headers_):
    content_type = _header(headers_, "content-type") or ""
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    return match.group(1) if match else None


def _sniff_charset(body):
    """响应头没写编码时从内容判断：BOM、meta 声明、再试常见编码。"""
    if body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    match = re.search(br"<meta[^>]+charset=[\"']?([\w.-]+)", body[:4096], re.I)
    if match:
        return match.group(1).decode("ascii", "ignore")
    for enc in ("utf-8", "gb18030"):
        try:
            body.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


class Negotiator:
    """按站点记住协商结果；fetch(site, accept_encoding) -> response dict。"""

    def __init__(self, fetch=None):
        self.fetch = fetch
        self.records = {}
        self.retained = {}  # site -> 解压失败的原始响应字节，保留到排查结束

    def record(self, site):
        return self.records.get(site)

    def headers(self, site):
        """按站点记忆出请求头；未协商过的站点先不压缩，避免被直接拒绝。"""
        rec = self.records.get(site)
        return {"accept-encoding": rec.accept_encoding if rec else "identity"}

    def negotiate(self, site):
        """探测站点支持的压缩与编码并记录；已锁定的人工配置不动。"""
        rec = self.records.get(site)
        if rec and rec.locked:
            return rec
        if self.fetch is None:
            raise NegotiationError("未提供 fetch，无法探测")
        last_error = None
        for enc in PREFERENCE:
            if not _supported(enc):
                last_error = f"{enc}: 本机不支持解压"
                continue
            try:
                resp = self.fetch(site, enc)
                body = resp.get("body", b"") or b""
                raw = _decompress(_content_encoding(resp), body)
                charset = _declared_charset(resp.get("headers")) or (
                    _sniff_charset(raw) if raw else None)
            except Exception as exc:  # 单个候选失败只换下一个
                last_error = f"{enc}: {exc}"
                continue
            rec = Record(site=site, accept_encoding=enc, charset=charset)
            self.records[site] = rec
            return rec
        # 全部失败：退回不压缩，错误留在记录里
        rec = Record(site=site, accept_encoding="identity", last_error=last_error)
        self.records[site] = rec
        return rec

    def selfcheck(self, site):
        """新站点接入自检；失败只影响这个站点，不向外抛。"""
        try:
            rec = self.negotiate(site)
            return {"site": site, "ok": rec.last_error is None,
                    "accept_encoding": rec.accept_encoding,
                    "charset": rec.charset, "error": rec.last_error}
        except Exception as exc:
            return {"site": site, "ok": False, "accept_encoding": None,
                    "charset": None, "error": str(exc)}

    def override(self, site, accept_encoding=None, charset=None):
        """人工覆盖协商结果并锁定，之后自动探测不再改回。"""
        rec = self.records.get(site) or Record(site=site)
        if accept_encoding is not None:
            rec.accept_encoding = accept_encoding
        if charset is not None:
            rec.charset = charset
        rec.locked = True
        rec.source = "manual"
        rec.updated_at = time.time()
        self.records[site] = rec
        return rec

    def unlock(self, site):
        """站点改版后解锁，下次 negotiate 重新探测。"""
        rec = self.records.get(site)
        if rec:
            rec.locked = False
        return rec

    def decode(self, response, site=None):
        """解压并按实际编码解码；解压失败保留原始字节并单独标出。"""
        body = response.get("body", b"") or b""
        headers_ = response.get("headers")
        rec = self.records.get(site) if site else None
        result = {"site": site,
                  "negotiated": rec.accept_encoding if rec else None,
                  "content_encoding": _content_encoding(response),
                  "decompressed": False, "encoding": None, "text": None,
                  "failed": False, "failure_reason": None,
                  "status": None, "raw": None}
        if not body:
            result.update(status="empty", text="")
            return result
        try:
            raw = _decompress(result["content_encoding"], body)
            result["decompressed"] = True
        except Exception as exc:
            result.update(failed=True, status="decompress_failed",
                          failure_reason=f"{result['content_encoding']}: {exc}",
                          raw=body)
            if site:
                self.retained.setdefault(site, []).append(body)
            return result
        charset = (_declared_charset(headers_) or (rec and rec.charset)
                   or _sniff_charset(raw))
        try:
            text = raw.decode(charset, "replace")
        except LookupError:  # 声明了不认识的编码，退回内容嗅探
            charset = _sniff_charset(raw)
            text = raw.decode(charset, "replace")
        result.update(status="ok", text=text, encoding=charset)
        return result

    def clear_retained(self, site):
        """排查结束后清掉该站点保留的失败原始响应。"""
        self.retained.pop(site, None)


_default = Negotiator()


def headers(site):
    return _default.headers(site)


def decode(response, site=None):
    return _default.decode(response, site=site)
