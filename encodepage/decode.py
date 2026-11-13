"""网页编码识别：响应头 -> 页面 meta -> 字节分布打分的多级回退。"""

import codecs
import re


REPLACEMENT_THRESHOLD = 0.05
HARD_FAILURE_RATIO = 0.5
CANDIDATE_ENCODINGS = ("utf-8", "gbk", "big5")
FALLBACK_ENCODING = "iso8859-1"
META_SCAN_BYTES = 4096

_META_RE = re.compile(
    br"""<meta[^>]+?charset[\s\x27\"]*=[\s\x27\"]*([A-Za-z0-9_.:-]+)""",
    re.IGNORECASE,
)
_XML_RE = re.compile(
    br"""<\?xml[^>]+?encoding[\s\x27\"]*=[\s\x27\"]*([A-Za-z0-9_.:-]+)""",
    re.IGNORECASE,
)
_ADJACENT_ENCODINGS = {
    "utf-8": ("gbk", "big5"),
    "gbk": ("utf-8", "big5"),
    "big5": ("gbk", "utf-8"),
}


class DecodedText(str):
    """解码结果：行为与普通 str 一致，附带识别元数据。"""

    def __new__(cls, value, encoding, confidence, warnings=None):
        obj = str.__new__(cls, value)
        obj.encoding = encoding
        obj.confidence = confidence
        obj.warnings = list(warnings or ())
        return obj

    def __repr__(self):
        return (
            "DecodedText(encoding=%r, confidence=%.2f, warnings=%r, text=%s)"
            % (self.encoding, self.confidence, self.warnings, str.__repr__(self))
        )


def _parse_header_charset(headers):
    content_type = headers.get("content-type") or headers.get("Content-Type") or ""
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip("\x27\"")
    return None


def _parse_meta_charset(body):
    head = body[:META_SCAN_BYTES]
    match = _META_RE.search(head) or _XML_RE.search(head)
    if match:
        return match.group(1).decode("ascii", errors="replace")
    return None


def _normalize_encoding(name):
    if not name:
        return None
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def _decode_metrics(body, encoding):
    """返回 (文本, 替换字符比例)；严格解码成功时比例为 0。"""
    try:
        return body.decode(encoding), 0.0
    except UnicodeDecodeError:
        text = body.decode(encoding, errors="replace")
        if not text:
            return text, 0.0
        return text, text.count("\ufffd") / len(text)


def _distribution_bonus(body, encoding):
    """字节分布加分（0~0.01），只在成功率并列时影响排序。"""
    if not body:
        return 0.0
    plausible = 0
    i = 0
    n = len(body)
    if encoding == "utf-8":
        while i + 1 < n:
            if (
                0xE0 <= body[i] <= 0xEF
                and i + 2 < n
                and 0x80 <= body[i + 1] <= 0xBF
                and 0x80 <= body[i + 2] <= 0xBF
            ):
                plausible += 1
                i += 3
            elif 0xC2 <= body[i] <= 0xDF and 0x80 <= body[i + 1] <= 0xBF:
                plausible += 1
                i += 2
            else:
                i += 1
    else:
        while i + 1 < n:
            lead, trail = body[i], body[i + 1]
            if 0x81 <= lead <= 0xFE and 0x40 <= trail <= 0xFE and trail != 0x7F:
                plausible += 1
                i += 2
            else:
                i += 1
    return min(plausible / n, 1.0) * 0.01


def _best_candidate(body, candidates=CANDIDATE_ENCODINGS):
    """按解码成功率为主、字节分布加分为辅挑选编码。"""
    best = None
    for encoding in candidates:
        text, ratio = _decode_metrics(body, encoding)
        score = (1.0 - ratio) + _distribution_bonus(body, encoding)
        if best is None or score > best[3]:
            best = (encoding, text, ratio, score)
    return best


def decode(body, headers):
    """识别并解码网页字节，返回带 encoding/confidence/warnings 的 DecodedText。"""
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise TypeError("body 必须是 bytes-like 对象")
    body = bytes(body)
    warnings = []

    if not body:
        return DecodedText("", "utf-8", 1.0)

    raw_header = _parse_header_charset(headers)
    header_charset = _normalize_encoding(raw_header)
    if raw_header and header_charset is None:
        warnings.append("响应头声明的编码 %r 无法识别，已忽略" % raw_header)

    raw_meta = _parse_meta_charset(body)
    meta_charset = _normalize_encoding(raw_meta)
    if raw_meta and meta_charset is None:
        warnings.append("页面 meta 声明的编码 %r 无法识别，已忽略" % raw_meta)

    encoding = text = ratio = None
    base_confidence = 0.9

    for declared, source, base in (
        (header_charset, "响应头", 0.99),
        (meta_charset, "页面 meta", 0.95),
    ):
        if declared is None:
            continue
        declared_text, declared_ratio = _decode_metrics(body, declared)
        if declared_ratio == 0.0:
            encoding, text, ratio = declared, declared_text, 0.0
            base_confidence = base
            break
        best = _best_candidate(body)
        warnings.append(
            "%s声明编码 %s 的解码成功率仅 %.1f%%，与实际字节冲突；"
            "按解码成功率改用 %s（%.1f%%）"
            % (
                source,
                declared,
                (1.0 - declared_ratio) * 100,
                best[0],
                (1.0 - best[2]) * 100,
            )
        )
        encoding, text, ratio = best[0], best[1], best[2]
        base_confidence = 0.8
        break
    else:
        encoding, text, ratio, _ = _best_candidate(body)

    if ratio >= HARD_FAILURE_RATIO:
        warnings.append(
            "所有候选编码解码成功率均低于 %.0f%%，使用 %s 单字节兜底，文本可能不可读"
            % ((1.0 - HARD_FAILURE_RATIO) * 100, FALLBACK_ENCODING)
        )
        return DecodedText(
            body.decode(FALLBACK_ENCODING), FALLBACK_ENCODING, 0.1, warnings
        )

    if ratio > REPLACEMENT_THRESHOLD:
        alt = _best_candidate(
            body, _ADJACENT_ENCODINGS[encoding] + (encoding,)
        )
        if alt[0] != encoding and alt[2] < ratio:
            warnings.append(
                "替换字符比例 %.1f%% 超过阈值 %.0f%%，尝试相邻编码后改用 %s（%.1f%%）"
                % (ratio * 100, REPLACEMENT_THRESHOLD * 100, alt[0], (1.0 - alt[2]) * 100)
            )
            encoding, text, ratio = alt[0], alt[1], alt[2]
        else:
            warnings.append(
                "替换字符比例 %.1f%% 超过阈值 %.0f%%，相邻编码无更优结果，保留 %s"
                % (ratio * 100, REPLACEMENT_THRESHOLD * 100, encoding)
            )
        base_confidence = 0.7

    confidence = round(base_confidence * (1.0 - ratio), 4)
    return DecodedText(text, encoding, confidence, warnings)
