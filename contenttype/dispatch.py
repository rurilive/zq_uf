"""响应类型分派：响应头、魔数、结构三层探测。

探测顺序：
1. 错误页指纹（状态码 >= 400 且正文含错误特征，或正文含强指纹如 Traceback）
2. Content-Type 响应头（text/plain、octet-stream 等泛型头不算结论，继续下探）
3. 魔数 / 二进制特征（前 512 字节）
4. 结构探测（JSON 解析、XML 声明/解析、HTML 标签）
5. URL 后缀兜底，最后默认 html
"""

import json as _json
import xml.etree.ElementTree as _ET

_SAMPLE_SIZE = 512
_SAMPLE_PREVIEW = 80

_TYPES = ("json", "xml", "html", "text", "error", "binary")

# 魔数 -> 二进制子类型
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
)

# 结论性 Content-Type 子串 -> 类型；text/plain、octet-stream 不在此列
_HEADER_TYPES = (
    ("json", "json"),
    ("xml", "xml"),
    ("html", "html"),
)

_ERROR_KEYWORDS = (
    "internal server error", "not found", "forbidden", "bad gateway",
    "service unavailable", "bad request", "unauthorized", "exception",
    "traceback", "error 4", "error 5", "<title>4", "<title>5",
)
_STRONG_ERROR_FINGERPRINTS = ("traceback (most recent call last)",)

_SUFFIX_TYPES = {".json": "json", ".xml": "xml", ".html": "html", ".htm": "html"}


class Detection(str):
    """分派结果：str 值为类型名，附带置信度、处理器、样本和原因。"""

    def __new__(cls, type_, confidence, sample="", reason=""):
        obj = str.__new__(cls, type_)
        obj.type = type_
        obj.confidence = confidence
        obj.handler = _HANDLERS.get(type_, _handle_text).__name__
        obj.sample = sample
        obj.reason = reason
        return obj

    def __repr__(self):
        return "Detection(%r, confidence=%.2f, handler=%s)" % (
            self.type, self.confidence, self.handler)


class Failure:
    """解析失败结果，携带失败原因，永不抛异常。"""

    def __init__(self, type_, reason):
        self.type = type_
        self.reason = reason

    def __bool__(self):
        return False

    def __repr__(self):
        return "Failure(%s: %s)" % (self.type, self.reason)


def _headers(response):
    raw = response.get("headers") or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def _status(response):
    return response.get("status") or response.get("status_code") or 200


def _raw_body(response):
    body = response.get("body")
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    return str(body).encode("utf-8", "replace")


def _text(response):
    return _raw_body(response).decode("utf-8", "replace")


def _sample_preview(raw):
    return raw[:_SAMPLE_PREVIEW].decode("utf-8", "replace")


def _looks_binary(sample):
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    non_text = sum(1 for b in sample if b < 0x09 or (0x0E <= b < 0x20) or b > 0x7E)
    return non_text / len(sample) > 0.30


def _detect_error(response, text):
    lowered = text.lower()
    if any(fp in lowered for fp in _STRONG_ERROR_FINGERPRINTS):
        return 0.9, "strong error fingerprint in body"
    if _status(response) >= 400 and any(k in lowered for k in _ERROR_KEYWORDS):
        return 0.95, "status %s with error fingerprint" % _status(response)
    return 0.0, ""


def _detect_header(response):
    ctype = _headers(response).get("content-type", "").lower()
    for key, type_ in _HEADER_TYPES:
        if key in ctype:
            return type_
    return None


def _detect_magic(raw):
    for magic, _name in _MAGIC:
        if raw.startswith(magic):
            return True
    return _looks_binary(raw[:_SAMPLE_SIZE])


def _detect_structure(text):
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] in "{[":
        try:
            _json.loads(stripped)
            return "json"
        except ValueError:
            return None
    if stripped.startswith("<?xml"):
        return "xml"
    lowered = stripped[:_SAMPLE_SIZE].lower()
    if "<!doctype html" in lowered or "<html" in lowered:
        return "html"
    if stripped.startswith("<"):
        try:
            _ET.fromstring(stripped)
            return "xml"
        except _ET.ParseError:
            return "html"
    return None


def _detect_suffix(response):
    url = (response.get("url") or "").lower().split("?")[0]
    for suffix, type_ in _SUFFIX_TYPES.items():
        if url.endswith(suffix):
            return type_
    return None


def dispatch(response):
    """按头、魔数、结构三层探测响应类型，返回 Detection。"""
    raw = _raw_body(response)
    text = raw.decode("utf-8", "replace")
    sample = _sample_preview(raw)

    conf, reason = _detect_error(response, text)
    if conf:
        return Detection("error", conf, sample, reason)

    header_type = _detect_header(response)
    if header_type:
        return Detection(header_type, 0.9, sample, "Content-Type header")

    if _detect_magic(raw):
        return Detection("binary", 0.95, sample, "magic bytes / binary heuristic")

    struct_type = _detect_structure(text)
    if struct_type:
        return Detection(struct_type, 0.8, sample, "body structure")

    suffix_type = _detect_suffix(response)
    if suffix_type:
        return Detection(suffix_type, 0.4, sample, "URL suffix fallback")

    return Detection("html", 0.2, sample, "no signal, default")


def _handle_json(response):
    return _json.loads(_text(response))


def _handle_xml(response):
    return _ET.fromstring(_text(response))


def _handle_text(response):
    return _text(response)


def _handle_binary(response):
    raw = _raw_body(response)
    expected = _headers(response).get("content-length")
    attachment = {
        "filename": _filename(response),
        "data": raw,
        "length": len(raw),
        "expected_length": int(expected) if expected else None,
    }
    if expected and int(expected) != len(raw):
        raise ValueError(
            "length mismatch: Content-Length %s, got %d" % (expected, len(raw)))
    return attachment


def _filename(response):
    url = (response.get("url") or "").split("?")[0].rstrip("/")
    name = url.rsplit("/", 1)[-1] if "/" in url else ""
    return name or "attachment.bin"


_HANDLERS = {
    "json": _handle_json,
    "xml": _handle_xml,
    "html": _handle_text,
    "text": _handle_text,
    "error": _handle_text,
    "binary": _handle_binary,
}


def parse(response):
    """按 dispatch 结果分派处理器解析；失败返回 Failure，不抛异常。"""
    det = dispatch(response)
    handler = _HANDLERS.get(det.type, _handle_text)
    try:
        return handler(response)
    except Exception as exc:
        return Failure(det.type, str(exc))
