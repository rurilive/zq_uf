"""编码还原。

浏览器在不同上下文里会先解码再渲染，检测时必须模拟这一步，
不能只拿响应原文做字符串比较。每层解码都返回 (结果, 是否变化)，
便于把完整还原过程写进结论。
"""

import html
import re
from urllib.parse import unquote

_JS_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)")
_WHITESPACE = set(" \t\n\r\f\v")
_ENTITY_BODY = re.compile(r"#?[0-9a-zA-Z]+$")


def decode_entities(text):
    """HTML 实体解码：&lt; -> <、&#60; -> <、&amp; -> &。"""
    out = html.unescape(text)
    return out, out != text


def decode_percent(text):
    """百分号解码：%3C -> <。URL 查询串里的 + 按空格处理。"""
    out = unquote(text)
    if "+" in text:
        out = out.replace("+", " ")
    return out, out != text


def _js_replace(match):
    seq = match.group(1)
    if seq.startswith("u{"):
        try:
            return chr(int(seq[2:-1], 16))
        except ValueError:
            return match.group(0)
    if seq.startswith(("u", "x")):
        try:
            return chr(int(seq[1:], 16))
        except ValueError:
            return match.group(0)
    simple = {
        "n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
        "v": "\v", "0": "\0", "\\": "\\", "'": "'", '"': '"', "`": "`",
        "/": "/",
    }
    return simple.get(seq, seq)


def decode_js_escape(text):
    """脚本字符串反转义：\\u003c -> <、\\x3c -> <、\\n -> 换行。"""
    out = _JS_ESCAPE.sub(_js_replace, text)
    return out, out != text


def restore(text, context):
    """按上下文模拟浏览器的解码顺序，返回 (还原结果, 还原链)。

    还原链记录每一层：层名、原文、还原后文本、是否发生变化。
    """
    chain = []

    current = text
    if context in ("html", "attribute", "url", "comment"):
        # 浏览器解析属性/正文时先做实体解码；URL 属性值再做百分号解码
        before = current
        current, changed = decode_entities(current)
        chain.append({"layer": "html_entity", "from": before,
                      "to": current, "changed": changed})
        if context == "url":
            before = current
            current, changed = decode_percent(current)
            chain.append({"layer": "percent", "from": before,
                          "to": current, "changed": changed})
    elif context == "script":
        # <script> 内容不做实体解码；字符串字面量里才做 JS 反转义
        before = current
        current, changed = decode_js_escape(current)
        chain.append({"layer": "js_escape", "from": before,
                      "to": current, "changed": changed})
    else:
        chain.append({"layer": "none", "from": current,
                      "to": current, "changed": False})
    return current, chain


def has_raw_quote(text, quote):
    """去掉合法 JS 转义后，是否还存在能闭合字符串的裸引号。"""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            # 反斜杠转义掉紧随其后的字符（含引号），整段跳过
            i += 2
            continue
        if ch == quote:
            return True
        i += 1
    return False


def has_raw_tag_close(text):
    """是否存在未转义的 </script 闭合（大小写不敏感）。"""
    lowered = text.lower()
    start = 0
    while True:
        idx = lowered.find("</script", start)
        if idx < 0:
            return False
        if idx == 0 or lowered[idx - 1] != "\\":
            return True
        start = idx + 1


def is_whitespace(ch):
    return ch in _WHITESPACE


def _entity_span(text, pos):
    """pos 处若为 HTML 实体，返回其原文跨度，否则返回 1。"""
    if text[pos] != "&":
        return 1
    semi = text.find(";", pos)
    if 0 < semi - pos <= 32 and _ENTITY_BODY.match(text[pos + 1:semi]):
        return semi - pos + 1
    return 1


def _percent_span(text, pos):
    """pos 处若为合法百分号编码，返回跨度 3，否则返回 1。"""
    if text[pos] == "%" and pos + 2 < len(text):
        try:
            int(text[pos + 1:pos + 3], 16)
            return 3
        except ValueError:
            return 1
    return 1


def decode_position_map(text, kind):
    """解码并返回 (解码结果, 映射表)。

    映射表 mapping[d] = 解码后位置 d 对应的原文起点；
    解码只缩短不增长，多字符编码序列整体映射到第一个解码字符。
    kind: 'entity' 或 'percent'。
    """
    span_fn = _entity_span if kind == "entity" else _percent_span
    decoder = decode_entities if kind == "entity" else decode_percent
    decoded, _ = decoder(text)
    mapping = []
    raw_pos = 0
    n_raw = len(text)
    while raw_pos < n_raw:
        span = span_fn(text, raw_pos)
        mapping.append(raw_pos)
        raw_pos += span
    mapping.append(n_raw)
    if len(mapping) != len(decoded) + 1:
        # 映射与实际解码不一致（边界输入），退化为不可信，调用方按原文处理
        return decoded, None
    return decoded, mapping
