"""页面结构解析与回显上下文定位。

不依赖第三方库，用状态机把页面切成互不重叠的区域：
HTML 正文、标签（含属性值区间）、脚本/样式原文块、注释、声明。
检测时按回显偏移找到所在区域，即回显上下文。
"""

import re

from .decode import is_whitespace

# 属性值会被浏览器当 URL 解析的属性
URL_ATTRS = {
    "href", "src", "action", "formaction", "data", "poster",
    "background", "cite", "longdesc", "manifest", "xlink:href",
}
# 内容按原文处理、不做 HTML 实体解码的标签
RAWTEXT_TAGS = {"script", "style"}

# HTML 空元素（void elements），没有闭合标签
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

_TAG_NAME = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9:_-]*)")
_ATTR_NAME = re.compile(r"[a-zA-Z_:][-a-zA-Z0-9_:.]*")


def _text(start, end):
    return {"type": "text", "start": start, "end": end}


def _parse_attrs(tag_text, base):
    """解析开标签内的属性，返回 {名称: [(值起点, 值终点, 引号), ...]}。

    偏移基于整份页面正文（base 为标签起点）。
    """
    attrs = {}
    n = len(tag_text)
    i = 1  # 跳过 '<'
    while i < n and not is_whitespace(tag_text[i]) and tag_text[i] not in "/>":
        i += 1
    while i < n:
        while i < n and (is_whitespace(tag_text[i]) or tag_text[i] == "/"):
            i += 1
        m = _ATTR_NAME.match(tag_text, i)
        if not m:
            i += 1
            continue
        name = m.group(0).lower()
        i = m.end()
        while i < n and is_whitespace(tag_text[i]):
            i += 1
        if i < n and tag_text[i] == "=":
            i += 1
            while i < n and is_whitespace(tag_text[i]):
                i += 1
            if i < n and tag_text[i] in "\"'":
                quote = tag_text[i]
                vs = i + 1
                ve = tag_text.find(quote, vs)
                if ve < 0:
                    ve = n
                attrs.setdefault(name, []).append((base + vs, base + ve, quote))
                i = ve + 1 if ve < n else n
            else:
                vs = i
                while i < n and not is_whitespace(tag_text[i]) and tag_text[i] != ">":
                    i += 1
                attrs.setdefault(name, []).append((base + vs, base + i, ""))
        else:
            # 布尔属性，没有值区间
            attrs.setdefault(name, []).append((base + i, base + i, ""))
    return attrs


def tokenize(body):
    """把页面切成区域列表，同时返回结构异常（解析失败）列表。"""
    regions = []
    parse_errors = []
    stack = []  # (标签名, 起点)，用于检测未闭合元素
    n = len(body)
    i = 0
    while i < n:
        lt = body.find("<", i)
        if lt < 0:
            regions.append(_text(i, n))
            break
        if lt > i:
            regions.append(_text(i, lt))
        if body.startswith("<!--", lt):
            end = body.find("-->", lt + 4)
            if end < 0:
                parse_errors.append({"offset": lt, "reason": "注释未闭合"})
                regions.append({"type": "comment", "start": lt, "end": n})
                break
            regions.append({"type": "comment", "start": lt, "end": end + 3})
            i = end + 3
            continue
        if body.startswith("<!", lt) or body.startswith("<?", lt):
            end = body.find(">", lt)
            if end < 0:
                parse_errors.append({"offset": lt, "reason": "声明未闭合"})
                regions.append({"type": "declaration", "start": lt, "end": n})
                break
            regions.append({"type": "declaration", "start": lt, "end": end + 1})
            i = end + 1
            continue
        m = _TAG_NAME.match(body, lt)
        if not m:
            # 孤立的 '<'，按正文字符处理
            regions.append(_text(lt, lt + 1))
            i = lt + 1
            continue
        tag = m.group(1).lower()
        is_close = body[lt:m.end()].lstrip("<").startswith("/")
        close = body.find(">", lt)
        if close < 0:
            parse_errors.append({"offset": lt, "reason": "标签未闭合: %s" % tag})
            regions.append(_text(lt, n))
            break
        tag_text = body[lt:close]
        self_closing = (not is_close) and tag_text.rstrip().endswith("/")
        tag_end = close + 1
        attrs = _parse_attrs(tag_text, lt)
        if tag in RAWTEXT_TAGS and not is_close:
            closing = re.compile(r"</\s*%s\s*>" % re.escape(tag), re.IGNORECASE)
            cm = closing.search(body, tag_end)
            regions.append({"type": "tag", "start": lt, "end": tag_end,
                            "tag": tag, "attrs": attrs})
            if not cm:
                parse_errors.append(
                    {"offset": lt, "reason": "%s 块缺少闭合标签" % tag})
                regions.append({"type": tag, "start": tag_end, "end": n,
                                "tag": tag, "attrs": attrs})
                break
            regions.append({"type": tag, "start": tag_end, "end": cm.start(),
                            "tag": tag, "attrs": attrs})
            regions.append({"type": "tag", "start": cm.start(), "end": cm.end(),
                            "tag": tag, "attrs": {}})
            i = cm.end()
            continue
        regions.append({"type": "tag", "start": lt, "end": tag_end,
                        "tag": tag, "attrs": attrs})
        if is_close:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == tag:
                    if depth != len(stack) - 1:
                        parse_errors.append({
                            "offset": lt,
                            "reason": "标签嵌套异常: </%s> 之前 <%s> 未闭合"
                            % (tag, stack[-1][0]),
                        })
                    del stack[depth:]
                    break
            else:
                parse_errors.append({
                    "offset": lt, "reason": "多余的闭合标签 </%s>" % tag})
        elif not self_closing and tag not in VOID_TAGS:
            stack.append((tag, lt))
        i = tag_end

    for tag, offset in stack:
        parse_errors.append({"offset": offset,
                             "reason": "标签未闭合: <%s>" % tag})
    parse_errors.sort(key=lambda item: item["offset"])
    return regions, parse_errors


def _script_string_state(script_text, pos):
    """判断脚本块内 pos 位置是否处于字符串字面量中。

    扫描在 pos 处停止：此时若在引号内，pos 属于字符串内容；
    pos 本身若是引号，它是开/闭引号，不算字符串内容。
    """
    quote = ""
    i = 0
    while i < pos and i < len(script_text):
        ch = script_text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        i += 1
    return {"in_string": bool(quote), "quote": quote, "at_quote":
            pos < len(script_text) and script_text[pos] in "\"'`"}


def _attr_at(region, offset):
    for name, values in region.get("attrs", {}).items():
        for vs, ve, quote in values:
            if vs <= offset < ve:
                return name, (vs, ve, quote)
    return None


def context_at(body, offset, regions):
    """返回偏移处的上下文描述：context 名称及标签、属性、引号等细节。"""
    for region in regions:
        if not (region["start"] <= offset < region["end"]):
            continue
        kind = region["type"]
        if kind == "text":
            return {"context": "html", "region": region}
        if kind == "comment":
            return {"context": "comment", "region": region}
        if kind == "declaration":
            return {"context": "unknown", "region": region,
                    "reason": "DOCTYPE/处理指令内回显"}
        if kind in ("script", "style"):
            local = offset - region["start"]
            return {
                "context": "script",
                "region": region,
                "tag": region.get("tag", kind),
                "string": _script_string_state(
                    body[region["start"]:region["end"]], local),
            }
        if kind == "tag":
            hit = _attr_at(region, offset)
            if hit:
                attr, (_vs, _ve, quote) = hit
                return {
                    "context": "url" if attr in URL_ATTRS else "attribute",
                    "region": region,
                    "tag": region.get("tag", ""),
                    "attr": attr,
                    "quote": quote,
                }
            return {"context": "tagname", "region": region,
                    "tag": region.get("tag", "")}
    return {"context": "unknown", "region": None, "reason": "偏移不在任何区域内"}
