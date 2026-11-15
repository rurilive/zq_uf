"""按站点协商压缩与文本编码。

公开接口保持两个：
- ``headers(site)``：返回该站点本轮请求应带的协商头；
- ``decode(response)``：按实际内容编码解压、按实际字符集解码。

协商结果按站点缓存在模块级注册表中，可用 :func:`override` 人工覆盖，
:func:`reset` 清空。压缩/解压失败不抛异常，单独标记并保留原始字节，
下一轮 :func:`headers` 自动回退到下一档编码，最终兜底 identity。

只用 Python 标准库；br 依赖可选的 brotli/brotlicffi，zstd 依赖
Python 3.14 的 zstd 模块或可选的 zstandard 包，缺失时不参与协商。
"""

import codecs
import gzip
import re
import zlib

# ---------------------------------------------------------------- 解码器注册

_DECODERS = {}          # content-coding -> callable(raw_bytes) -> bytes
_UNAVAILABLE = {}       # content-coding -> 缺失原因（环境不支持时）


def _register_gzip(raw):
    return gzip.decompress(raw)


def _register_deflate(raw):
    # 服务端可能给 zlib 包装的 deflate，也可能给裸 deflate，两种都试。
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return zlib.decompress(raw, -zlib.MAX_WBITS)


_DECODERS["gzip"] = _register_gzip
_DECODERS["deflate"] = _register_deflate

try:
    import brotli as _brotli
except ImportError:
    try:
        import brotlicffi as _brotli
    except ImportError:
        _brotli = None
if _brotli is not None:
    _DECODERS["br"] = _brotli.decompress
else:
    _UNAVAILABLE["br"] = "缺少 brotli/brotlicffi 依赖"

try:
    import zstandard as _zstd_mod
except ImportError:
    _zstd_mod = None
if _zstd_mod is not None:
    def _zstd_decompress(raw):
        return _zstd_mod.ZstdDecompressor().decompress(raw)
    _DECODERS["zstd"] = _zstd_decompress
else:
    try:
        import zstd as _py314_zstd          # Python 3.14+ 标准库
    except ImportError:
        _py314_zstd = None
    if _py314_zstd is not None:
        _DECODERS["zstd"] = _py314_zstd.decompress
    else:
        _UNAVAILABLE["zstd"] = "需要 Python 3.14 的 zstd 模块或 zstandard 包"


def available_encodings():
    """当前环境可解压的 content-coding（不含 identity）。"""
    return tuple(_DECODERS)


# ------------------------------------------------------------ 站点协商注册表

_REGISTRY = {}

# 探测优先级：兼容性最广的 gzip 在前，identity 永远兜底。
# br/zstd 仅在环境支持时挂在 deflate 之后。
_PROBE_ORDER = ["gzip", "deflate"]
for _opt in ("br", "zstd"):
    if _opt in _DECODERS:
        _PROBE_ORDER.append(_opt)
_PROBE_ORDER.append("identity")


def _new_profile():
    return {
        "state": "probing",          # probing / locked
        "encoding": None,            # 已确认可用的 content-coding
        "charset": None,             # 已确认的字符集
        "failed_encodings": [],      # 本轮探测已失败的编码（有序）
        "source": "auto",            # auto / manual
        "reason": None,              # 最近一次失败原因
    }


def _get_profile(site):
    profile = _REGISTRY.get(site)
    if profile is None:
        profile = _new_profile()
        _REGISTRY[site] = profile
    return profile


def reset(site=None):
    """清空协商缓存。不传 site 清空全部站点。"""
    if site is None:
        _REGISTRY.clear()
    else:
        _REGISTRY.pop(site, None)


def override(site, encoding="identity", charset=None):
    """人工指定站点的压缩编码与字符集，绕过自动探测。

    encoding 必须是当前环境支持的 content-coding 或 identity。
    """
    if encoding != "identity" and encoding not in _DECODERS:
        reason = _UNAVAILABLE.get(encoding, "未知 content-coding")
        raise ValueError("不支持的编码 %r：%s" % (encoding, reason))
    if charset is not None:
        codecs.lookup(charset)            # 提前拒绝非法字符集
    profile = _get_profile(site)
    profile.update({
        "state": "locked",
        "encoding": encoding,
        "charset": charset,
        "failed_encodings": [],
        "source": "manual",
        "reason": None,
    })
    return dict(profile)


def profile(site):
    """返回站点协商状态的副本（探测进度、缓存结果、失败原因）。"""
    if site not in _REGISTRY:
        return None
    return dict(_REGISTRY[site])


def profiles():
    """返回全部站点协商状态的副本。"""
    return {site: dict(p) for site, p in _REGISTRY.items()}


# ------------------------------------------------------------------ 请求协商

def _next_probe(profile):
    for coding in _PROBE_ORDER:
        if coding in profile["failed_encodings"]:
            continue
        return coding
    return "identity"


def headers(site):
    """返回该站点本轮请求应带的头。

    未探测/探测中：只发当前探测档（gzip 起步，逐档回退）；一旦出现过
    失败，后续探测附带 identity 兜底；已锁定则发确认可用的编码。
    """
    profile = _get_profile(site)
    if profile["state"] == "locked":
        coding = profile["encoding"]
    else:
        coding = _next_probe(profile)
    if coding == "identity":
        accept = "identity"
    elif profile["state"] == "locked" or profile["failed_encodings"]:
        accept = "%s, identity" % coding
    else:
        accept = coding
    head = {"accept-encoding": accept}
    if profile["charset"]:
        head["accept-charset"] = profile["charset"]
    return head


# ------------------------------------------------------------- 响应解压/解码

def _header(headers_dict, name):
    """大小写不敏感取响应头。"""
    if not headers_dict:
        return None
    lowered = name.lower()
    for key, value in headers_dict.items():
        if key.lower() == lowered:
            return value
    return None


def _decompress(raw, codings):
    """按 content-encoding 列表逐层解压，返回 (结果字节, 失败档位或 None)。"""
    data = raw
    for coding in reversed(codings):        # 最后应用的编码最先解开
        if coding == "identity":
            continue
        decoder = _DECODERS.get(coding)
        if decoder is None:
            return data, coding
        try:
            data = decoder(data)
        except Exception as exc:            # gzip/zlib/brotli 各自的异常
            return data, "%s(%s)" % (coding, type(exc).__name__)
    return data, None


_CHARSET_RE = re.compile(
    rb"""charset\s*=\s*["']?\s*([A-Za-z0-9][A-Za-z0-9._:-]*)""", re.IGNORECASE)
_META_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9][A-Za-z0-9._:-]*)""",
    re.IGNORECASE)


def _detect_charset(raw, content_type):
    """字符集探测：头参数 -> BOM -> 页面声明 -> utf-8 校验 -> latin-1。"""
    if content_type:
        match = _CHARSET_RE.search(content_type.encode("latin-1", "replace"))
        if match:
            return match.group(1).decode("ascii").lower(), "content-type"
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", "bom"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16", "bom"
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        return "utf-32", "bom"
    head = raw[:4096]
    match = _META_RE.search(head)
    if not match:
        match = re.search(rb"""<\?xml[^>]+encoding\s*=\s*["']([^"']+)""", head)
    if match:
        return match.group(1).decode("ascii").lower(), "meta"
    try:
        raw.decode("utf-8")
        return "utf-8", "sniff"
    except UnicodeDecodeError:
        return "latin-1", "sniff"


def _record(site, profile, requested, actual, charset, reason):
    """根据本轮结果推进站点协商状态机。

    requested 是本轮请求发出的探测档，actual 是响应实际使用的编码。
    - 成功拿到 identity：站点不支持（或不再支持）压缩，锁定 identity；
    - 成功拿到与请求档一致的压缩编码：锁定该档；
    - 解压失败：请求档记入失败档，下一轮 headers() 自动换档重试；
    - identity 本身也失败：保持探测态，identity 继续兜底，不阻塞重试。
    """
    profile["charset"] = charset
    if reason is not None:
        profile["reason"] = reason
        if requested != "identity" and requested not in profile["failed_encodings"]:
            profile["failed_encodings"].append(requested)
        return
    profile["reason"] = None
    if actual == "identity":
        if requested != "identity" and requested not in profile["failed_encodings"]:
            profile["failed_encodings"].append(requested)
        profile["state"] = "locked"
        profile["encoding"] = "identity"
    elif actual == requested:
        profile["state"] = "locked"
        profile["encoding"] = actual
    elif requested not in profile["failed_encodings"]:
        # 请求压缩档却收到别的压缩档：请求档不被支持，继续回退探测。
        profile["failed_encodings"].append(requested)


def decode(response):
    """解压并解码一次响应。

    response 需含 ``body``（bytes），可选 ``site`` 与 ``headers``。
    永不抛异常：解压/解码失败时 failed=True、reason 写明原因、
    raw 保留原始字节，供下一轮 identity 重试与事后排查。
    """
    site = response.get("site")
    profile = _get_profile(site) if site is not None else _new_profile()
    requested = profile["encoding"] if profile["state"] == "locked" \
        else _next_probe(profile)
    raw = response.get("body", b"")
    if not isinstance(raw, (bytes, bytearray)):
        raw = b"" if raw is None else str(raw).encode("utf-8", "replace")
    raw = bytes(raw)

    ce_header = _header(response.get("headers"), "content-encoding")
    codings = [part.strip().lower() for part in ce_header.split(",")] \
        if ce_header else []
    codings = [c for c in codings if c]
    used = ", ".join(codings) if codings else "identity"

    result = {
        "site": site,
        "negotiation": profile["state"],
        "requested": requested,
        "accept_sent": headers(site)["accept-encoding"] if site is not None
                       else None,
        "encoding": used,
        "charset": None,
        "charset_source": None,
        "text": "",
        "decompressed": None,
        "raw": raw,                    # 原始字节保留到排查结束
        "failed": False,
        "reason": None,
    }

    payload, failed_coding = _decompress(raw, codings)
    if failed_coding is not None:
        reason = "解压失败: %s" % failed_coding
        result.update(failed=True, reason=reason, decompressed=None)
        if site is not None:
            _record(site, profile, requested, used, None, reason)
        return result

    content_type = _header(response.get("headers"), "content-type")
    if profile["charset"]:
        charset, charset_source = profile["charset"], "cached"
    else:
        charset, charset_source = _detect_charset(payload, content_type)
    try:
        text = payload.decode(charset, "replace")
    except (LookupError, TypeError):
        charset, charset_source, text = "latin-1", "sniff", \
            payload.decode("latin-1", "replace")

    result.update(charset=charset, charset_source=charset_source,
                  text=text, decompressed=payload)
    if site is not None:
        _record(site, profile, requested, used, charset, None)
    return result
