"""编码识别、解码与乱码比例检测，全部基于标准库。"""

import codecs
import re

_CHARSET_RE = re.compile(rb'charset=["\']?\s*([\w\-]+)', re.IGNORECASE)
_META_RE = re.compile(rb'<meta[^>]+>', re.IGNORECASE)


def detect_charset(body, content_type=""):
    """依次按 HTTP 头、meta 声明、BOM、经验兜底推断编码。"""
    header_match = _CHARSET_RE.search(content_type.encode("ascii", "ignore"))
    if header_match:
        return _normalize(header_match.group(1).decode("ascii"))
    head = body[:4096]
    for meta in _META_RE.findall(head):
        meta_match = _CHARSET_RE.search(meta)
        if meta_match:
            return _normalize(meta_match.group(1).decode("ascii"))
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        head.decode("gb18030")
        return "gb18030"
    except UnicodeDecodeError:
        return "latin-1"


def decode_body(body, content_type=""):
    """返回解码文本、实际编码、是否含替换符。"""
    if isinstance(body, str):
        return body, "utf-8", "\ufffd" in body
    charset = detect_charset(body, content_type)
    try:
        text = body.decode(charset)
    except (UnicodeDecodeError, LookupError):
        charset = "utf-8"
        text = body.decode("utf-8", errors="replace")
    return text, charset, "\ufffd" in text


def garbled_ratio(text):
    """替换符与典型乱码标记字符占可见字符的比例。"""
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return 0.0
    garbled = 0
    for char in visible:
        if char in _MOJIBAKE_MARKERS:
            garbled += 1
    return round(garbled / len(visible), 4)


_MOJIBAKE_MARKERS = {"\ufffd", "ï", "¿", "½", "Ã", "Â"}


def _normalize(name):
    name = name.strip().lower()
    aliases = {
        "gb2312": "gb18030",
        "gbk": "gb18030",
        "x-gbk": "gb18030",
        "zh-cn": "gb18030",
        "utf8": "utf-8",
        "u8": "utf-8",
    }
    return aliases.get(name, name)
