"""正文块提取。

脚本、样式、广告位和计数器在解析阶段整棵剪掉，不进入正文块；
正文按块标签（段落、标题、列表项等）切分，文本归一化后取稳定指纹。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from html.parser import HTMLParser

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# 这些节点连同子树一起丢弃：脚本、样式以及页面元信息。
SKIP_TAGS = {
    "script", "style", "noscript", "template", "iframe", "svg", "canvas",
    "head", "title", "meta", "link", "base",
}

# 正文推导所依赖的块级标签。
BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre",
    "dd", "dt", "figcaption", "caption", "summary", "address", "td", "th",
}

# 容器标签本身不是一段正文，但若其中没有块级子节点，就把整块文本
# 当作匿名段落处理（很多页面直接用 div 承载正文）。
CONTAINER_TAGS = {
    "div", "section", "article", "main", "aside", "header", "footer",
    "nav", "ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr",
    "figure", "form", "fieldset",
}

# class / id / data-* 里出现这些词时，视为广告或计数器，不参与比对。
IGNORE_TOKENS = {
    "ad", "ads", "advert", "adverts", "advertisement", "advertising",
    "adsbygoogle", "adslot", "adunit", "adcontainer", "adwrapper", "dfp",
    "sponsor", "sponsored", "sponsorship", "promo", "promoted", "promotion",
    "counter", "hitcount", "hitcounter", "pageview", "pageviews",
    "viewcount", "visitcounter", "visitorcounter",
}

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_DIGITS_RE = re.compile(r"\d+")
_ZERO_WIDTH = str.maketrans("", "", "​‌‍﻿")

# 段落里出现这些词且只有数字不同，视为计数器刷新，不算正文变化。
COUNTER_HINT_RE = re.compile(
    r"浏览|阅读|点击|访问|观看|播放|人气|热度|"
    r"\bviews?\b|\bvisits?\b|\bhits?\b|\bclicks?\b|\breads?\b|"
    r"\bcounter\b|\bpageviews?\b",
    re.IGNORECASE,
)


def is_counter_noise(old_text, new_text):
    """判断两段文本是否只是计数器数字不同（其余完全一致）。"""
    old_text = normalize_text(old_text)
    new_text = normalize_text(new_text)
    if old_text == new_text:
        return False
    if not COUNTER_HINT_RE.search(old_text) and not COUNTER_HINT_RE.search(new_text):
        return False
    if not _DIGITS_RE.search(old_text) and not _DIGITS_RE.search(new_text):
        return False
    return _DIGITS_RE.sub("", old_text) == _DIGITS_RE.sub("", new_text)



def normalize_text(text):
    """归一化正文：兼容字符、去零宽字符、折叠空白。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def fingerprint(text):
    """段落稳定指纹：只取决于归一化后的文本，与属性和空白无关。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def block_fingerprint(text):
    """块的指纹；计数器类段落忽略数字，避免每次刷新都算改版。"""
    normalized = normalize_text(text)
    if COUNTER_HINT_RE.search(normalized) and _DIGITS_RE.search(normalized):
        normalized = _DIGITS_RE.sub("", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def content_hash(blocks):
    """整份正文的指纹，由块的标签与段落指纹组成。"""
    payload = "\n".join(
        "{}:{}".format(block.get("tag", ""), block.get("fingerprint", ""))
        for block in blocks
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def snippet(text, limit=60):
    """给告警用的前后摘要。"""
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _tokens(value):
    return [token for token in _TOKEN_RE.split((value or "").lower()) if token]


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node(None, {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


class _Extractor:
    def __init__(self, ignore=None):
        self.ignore_tokens = set(IGNORE_TOKENS)
        for item in ignore or ():
            self.ignore_tokens.update(_tokens(str(item)))
        self._ignored_cache = {}
        self._block_cache = {}

    def is_ignored(self, node):
        cache_key = id(node)
        if cache_key in self._ignored_cache:
            return self._ignored_cache[cache_key]
        ignored = self._is_ignored(node)
        self._ignored_cache[cache_key] = ignored
        return ignored

    def _is_ignored(self, node):
        if node.tag in SKIP_TAGS:
            return True
        tokens = set()
        for name in ("class", "id"):
            tokens.update(_tokens(node.attrs.get(name)))
        # data-ad-slot / data-adsbygoogle-status 之类的属性名同样是信号。
        for name in node.attrs:
            if name.startswith("data-") or name.startswith("aria-"):
                tokens.update(_tokens(name))
        tokens.update(_tokens(node.attrs.get("aria-label")))
        return bool(tokens & self.ignore_tokens)

    def contains_block(self, node):
        cache_key = id(node)
        if cache_key in self._block_cache:
            return self._block_cache[cache_key]
        found = False
        for child in node.children:
            if isinstance(child, str) or self.is_ignored(child):
                continue
            if child.tag in BLOCK_TAGS or self.contains_block(child):
                found = True
                break
        self._block_cache[cache_key] = found
        return found

    def inline_text(self, node):
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif self.is_ignored(child):
                continue
            elif child.tag in BLOCK_TAGS:
                continue
            elif child.tag == "br":
                parts.append(" ")
            elif child.tag in VOID_TAGS:
                continue
            else:
                parts.append(self.inline_text(child))
        return "".join(parts)

    def _append(self, out, tag, text):
        text = normalize_text(text)
        if text:
            out.append({"tag": tag, "text": text, "fingerprint": block_fingerprint(text)})

    def _flush(self, parts, out, tag="text"):
        if parts:
            self._append(out, tag, "".join(parts))
            parts.clear()

    def collect(self, node, out):
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif self.is_ignored(child):
                continue
            elif child.tag in BLOCK_TAGS:
                self._flush(parts, out)
                self._append(out, child.tag, self.inline_text(child))
                self.collect_nested(child, out)
            elif child.tag == "br":
                parts.append(" ")
            elif child.tag in VOID_TAGS:
                continue
            elif self.contains_block(child):
                self._flush(parts, out)
                self.collect(child, out)
            elif child.tag in CONTAINER_TAGS:
                # 没有块级子节点的容器，本身就是一段正文。
                self._flush(parts, out)
                self._append(out, child.tag, self.inline_text(child))
            else:
                parts.append(self.inline_text(child))
        self._flush(parts, out)

    def collect_nested(self, node, out):
        """块节点内部只继续找块级子节点，避免重复收集内联文本。"""
        for child in node.children:
            if isinstance(child, str) or self.is_ignored(child):
                continue
            if child.tag in BLOCK_TAGS:
                self._append(out, child.tag, self.inline_text(child))
                self.collect_nested(child, out)
            elif self.contains_block(child):
                self.collect_nested(child, out)

    def extract(self, html):
        builder = _TreeBuilder()
        builder.feed(html or "")
        builder.close()
        blocks = []
        self.collect(builder.root, blocks)
        for index, block in enumerate(blocks):
            block["index"] = index
        return blocks


def extract_blocks(html, ignore=None):
    """把 HTML 切成正文块；ignore 可追加需要剔除的 class/id 关键词。"""
    return _Extractor(ignore=ignore).extract(html)
