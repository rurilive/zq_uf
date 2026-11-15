"""按站点协商压缩与编码：首访探测、按站记忆、失败回退 identity、人工覆盖锁定。"""

from __future__ import annotations

import codecs
import gzip
import re
import zlib
from dataclasses import dataclass, field

try:  # brotli 为可选依赖
    import brotli
except ImportError:
    brotli = None

try:  # Python 3.14+ 标准库
    from compression import zstd as _zstd
except ImportError:
    try:
        import zstandard as _zstd
    except ImportError:
        _zstd = None


def _need(name):
    def _missing(data):
        raise RuntimeError(f"{name} 解码器不可用")
    return _missing


def _zstd_decompress(data):
    if hasattr(_zstd, "decompress"):
        return _zstd.decompress(data)
    return _zstd.ZstdDecompressor().decompress(data)


_DECOMPRESSORS = {
    "gzip": gzip.decompress,
    "deflate": zlib.decompress,
    "br": brotli.decompress if brotli else _need("brotli"),
    "zstd": _zstd_decompress if _zstd else _need("zstd"),
    "identity": lambda d: d,
}

# 探测时的提供顺序，只提供本机确实能解的
_OFFER_ORDER = ("br", "zstd", "gzip")
_AVAILABLE = {"br": brotli is not None, "zstd": _zstd is not None, "gzip": True}

_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([\w\-]+)", re.I)
_HEADER_CHARSET = re.compile(r"charset=([\w\-]+)", re.I)


def offer_list():
    """本机可解压、可用于探测的压缩方式。"""
    return [c for c in _OFFER_ORDER if _AVAILABLE[c]]


def detect_charset(body, content_type=""):
    """按 响应头 → BOM → meta → 内容试探 的顺序判断编码，返回 (编码, 依据)。"""
    m = _HEADER_CHARSET.search(content_type or "")
    if m:
        return m.group(1).lower(), "header"
    for bom, name in _BOMS:
        if body.startswith(bom):
            return name, "bom"
    m = _META_CHARSET.search(body[:4096])
    if m:
        return m.group(1).decode("ascii", "ignore").lower(), "meta"
    try:
        body.decode("utf-8")
        return "utf-8", "content"
    except UnicodeDecodeError:
        return "gb18030", "content"  # 中文站点常见兜底


def _header(headers_, name, default=""):
    for k, v in (headers_ or {}).items():
        if k.lower() == name:
            return v
    return default


@dataclass
class SiteState:
    accept_encoding: str | None = None  # 协商出的压缩方式
    charset: str | None = None          # 协商出的编码
    probed: bool = False                # 是否已完成探测
    locked: bool = False                # 人工覆盖锁定，锁定后不再自动改回
    failures: list = field(default_factory=list)  # 最近失败原因
    last: dict | None = None            # 最近一次协商结果


class Negotiator:
    """按站点保存协商记录；站点之间互不影响。"""

    def __init__(self):
        self._sites = {}
        self._failed_raw = {}  # site -> [解压失败的原始响应]，保留到人工排查结束

    def _state(self, site):
        return self._sites.setdefault(site, SiteState())

    def headers(self, site):
        """未知站点发全量探测；已知站点按协商结果；失败过的站点回退 identity。"""
        st = self._sites.get(site)
        if st is None or not st.probed:
            offer = offer_list()
            ae = ", ".join(offer) if offer else "identity"
        else:
            ae = st.accept_encoding or "identity"
        return {"accept-encoding": ae}

    def decode(self, site, response):
        """解压并按实际编码解码；解压失败保留原始字节并单独标出。"""
        st = self._state(site)
        hdrs = response.get("headers", {})
        ce = (_header(hdrs, "content-encoding") or "identity").lower()
        body = response.get("body", b"")

        result = {"status": "ok", "ok": True, "text": None, "encoding": None,
                  "charset_source": None, "content_encoding": ce,
                  "decompressed": False, "failure": None, "raw": None}

        if not body:
            result.update(status="empty", ok=False,
                          failure={"stage": "empty", "reason": "响应体为空"})
            st.last = result
            return result

        try:
            raw = _DECOMPRESSORS.get(ce, _need(ce))(body)
            result["decompressed"] = True
        except Exception as exc:  # 解压失败：保留原始字节，不能当空页面
            reason = f"{ce}: {exc}"
            result.update(status="decompress_failed", ok=False, raw=body,
                          failure={"stage": "decompress", "reason": reason})
            st.failures.append(reason)
            self._failed_raw.setdefault(site, []).append(response)
            if not st.locked:  # 未锁定时回退不压缩
                st.accept_encoding, st.probed = "identity", True
            st.last = result
            return result

        if st.locked and st.charset:
            charset, source = st.charset, "override"
        else:
            charset, source = detect_charset(raw, _header(hdrs, "content-type"))
        result["encoding"], result["charset_source"] = charset, source
        try:
            result["text"] = raw.decode(charset)
        except (UnicodeDecodeError, LookupError) as exc:
            result.update(status="decode_failed", ok=False, raw=raw,
                          text=raw.decode("utf-8", "replace"),
                          failure={"stage": "decode",
                                   "reason": f"{charset}: {exc}"})
            st.failures.append(result["failure"]["reason"])
            st.last = result
            return result

        if not st.locked:  # 记录最近一次协商结果
            st.accept_encoding, st.charset, st.probed = ce, charset, True
        st.last = result
        return result

    def selfcheck(self, site, probe_response=None):
        """新站点接入自检；失败只记录并回退本站点，不影响其他站点。"""
        report = {"site": site, "offered": offer_list(), "ok": True,
                  "detail": None}
        if probe_response is not None:
            result = self.decode(site, probe_response)
            report["decode"] = result
            if not result["ok"]:
                report.update(ok=False, detail=result["failure"])
        return report

    def override(self, site, accept_encoding=None, charset=None):
        """人工覆盖协商结果并锁定，锁定后不再自动改回。"""
        st = self._state(site)
        if accept_encoding is not None:
            st.accept_encoding = accept_encoding
        if charset is not None:
            st.charset = charset
        st.locked = st.probed = True
        return self.record(site)

    def unlock(self, site):
        """站点改版后解锁，下次访问重新探测。"""
        st = self._state(site)
        st.locked = st.probed = False
        st.accept_encoding = st.charset = None
        return self.record(site)

    def record(self, site):
        st = self._state(site)
        return {"site": site, "accept_encoding": st.accept_encoding,
                "charset": st.charset, "probed": st.probed,
                "locked": st.locked, "failures": list(st.failures),
                "last": st.last}

    def failed_responses(self, site):
        """解压失败保留的原始响应，供排查。"""
        return list(self._failed_raw.get(site, []))

    def clear_failed(self, site):
        """排查结束后清除保留的原始响应。"""
        self._failed_raw.pop(site, None)


def headers(site):
    return default.headers(site)


def decode(site, response):
    return default.decode(site, response)


default = Negotiator()
