"""可配置的网页正文抽取清洗链。

流程：解析 -> 剪枝（脚本/导航/页脚/推荐等）-> 切块（表格按行转文本）
-> 按块打分过滤（阈值随文档长度自适应）-> 相邻去重 -> 输出结果。
"""
import re
from html.parser import HTMLParser

DEFAULT_RULES = {
    # 连同子树直接删除的标签
    "drop_tags": ("head", "script", "style", "noscript", "template", "iframe", "svg", "form"),
    # 结构性噪声区域（导航、页脚、侧栏等）
    "drop_sections": ("nav", "footer", "aside", "header"),
    # class/id 命中任一关键词即删除（导航、评论、相关推荐等）
    "drop_keywords": (
        "nav", "menu", "footer", "sidebar", "related", "recommend",
        "comment", "promo", "advert", "breadcrumb", "share", "copyright",
    ),
    # 常规页面保留段落的最少字数
    "min_block_chars": 15,
    # 可见文本总字数低于该值视为短页面
    "short_page_chars": 600,
    # 短页面保留段落的最少字数（降低阈值避免过度裁剪）
    "short_page_min_chars": 4,
    # 链接文字占比上限，超过视为导航/推荐列表
    "link_density_max": 0.5,
    # 是否合并相邻重复段落
    "dedupe_adjacent": True,
    # 发布时间匹配规则，按顺序尝试，命中即返回
    "date_patterns": (
        r"<meta[^>]+(?:article:published_time|pubdate|publishdate)[^>]+content=[\"']([^\"']+)",
        r"<time[^>]+datetime=[\"']([^\"']+)",
        r"(\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2}日?)",
    ),
}

_BLOCK_TAGS = frozenset((
    "p", "div", "section", "article", "main", "body", "li", "blockquote",
    "pre", "caption", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
))
_VOID_TAGS = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
))
_WS = re.compile(r"\s+")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []


class _Block:
    __slots__ = ("text", "tag", "link_chars")

    def __init__(self, text, tag, link_chars):
        self.text = text
        self.tag = tag
        self.link_chars = link_chars


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _parse(html):
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _text_of(node):
    parts = []
    _collect_text(node, parts)
    return _WS.sub(" ", "".join(parts)).strip()


def _collect_text(node, parts):
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            _collect_text(child, parts)


def _link_chars(node):
    total = 0
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == "a":
                total += len(_text_of(child))
            else:
                total += _link_chars(child)
    return total


def _prune(node, drop):
    kept = []
    for child in node.children:
        if isinstance(child, _Node):
            if drop(child):
                continue
            _prune(child, drop)
        kept.append(child)
    node.children = kept


def _noise_dropper(rules):
    tags = frozenset(rules["drop_tags"]) | frozenset(rules["drop_sections"])
    keywords = tuple(rules["drop_keywords"])

    def drop(node):
        if node.tag in tags:
            return True
        ident = (node.attrs.get("class", "") + " " + node.attrs.get("id", "")).lower()
        return bool(keywords) and any(key in ident for key in keywords)

    return drop


def _has_inner_block(node):
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag in _BLOCK_TAGS or child.tag == "table":
                return True
            if _has_inner_block(child):
                return True
    return False


def _table_rows(table):
    rows = []
    for child in table.children:
        if isinstance(child, _Node) and child.tag in ("tr", "thead", "tbody", "tfoot"):
            if child.tag == "tr":
                cells = [
                    _text_of(cell) for cell in child.children
                    if isinstance(cell, _Node) and cell.tag in ("td", "th")
                ]
                line = " | ".join(cell for cell in cells if cell)
                if line:
                    rows.append(line)
            else:
                rows.extend(_table_rows(child))
    return rows


def _iter_blocks(node):
    if node.tag == "table":
        for row in _table_rows(node):
            yield _Block(row, "tr", 0)
        return
    if node.tag in _BLOCK_TAGS and not _has_inner_block(node):
        text = _text_of(node)
        if text:
            yield _Block(text, node.tag, _link_chars(node))
        return
    for child in node.children:
        if isinstance(child, _Node):
            yield from _iter_blocks(child)


def _filter_blocks(blocks, rules, visible_chars):
    short_page = visible_chars < rules["short_page_chars"]
    threshold = rules["short_page_min_chars"] if short_page else rules["min_block_chars"]
    kept = []
    for block in blocks:
        text = block.text
        if block.tag == "tr":
            kept.append(block)  # 表格行不按字数阈值过滤
            continue
        density = block.link_chars / max(len(text), 1)
        if density > rules["link_density_max"]:
            continue
        if len(text) >= threshold:
            kept.append(block)
    return kept


def _dedupe(blocks):
    out = []
    prev = None
    for block in blocks:
        key = _WS.sub("", block.text)
        if not key:
            continue
        if key != prev:
            out.append(block)
        prev = key
    return out


def _extract_published(html, rules):
    for pattern in rules["date_patterns"]:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1).strip()
    return ""


def _first_h1(node):
    if node.tag == "h1":
        return _text_of(node)
    for child in node.children:
        if isinstance(child, _Node):
            text = _first_h1(child)
            if text:
                return text
    return ""


def extract_title(html):
    match = _TITLE.search(html)
    return match.group(1).strip() if match else ""


def _merge_rules(rules):
    merged = dict(DEFAULT_RULES)
    if rules:
        merged.update(rules)
    return merged


def extract(html, rules=None):
    """抽取网页正文，返回标题、发布时间、正文段落和置信度。"""
    rules = _merge_rules(rules)
    root = _parse(html)
    fallback_title = _first_h1(root)

    drop_tags = frozenset(rules["drop_tags"])
    _prune(root, lambda node: node.tag in drop_tags)
    visible_chars = len(_text_of(root))
    _prune(root, _noise_dropper(rules))

    blocks = list(_iter_blocks(root))
    blocks = _filter_blocks(blocks, rules, visible_chars)
    if rules["dedupe_adjacent"]:
        blocks = _dedupe(blocks)

    paragraphs = [block.text for block in blocks]
    kept_chars = sum(len(text) for text in paragraphs)
    confidence = min(round(kept_chars / visible_chars, 3), 1.0) if visible_chars else 0.0
    return {
        "title": extract_title(html) or fallback_title,
        "published": _extract_published(html, rules),
        "paragraphs": paragraphs,
        "confidence": confidence,
    }
