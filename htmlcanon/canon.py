"""DOM 级 HTML 规范化与稳定指纹（纯标准库）。

公开接口保持不变：
    canonical(html) -> str           规范化文本
    same(a, b)      -> bool          两个页面是否属于同一等价类
另提供：
    analyze(html)   -> CanonResult   规范化文本 + 规则命中 + 块指纹 + 等价类

设计要点见 README：标签小写、属性白名单与排序、空白折叠、自闭合统一、
剔除脚本/样式/注释/隐藏节点，按块生成 SHA256 指纹并合并。
规范化结果的每个块都带原文字符区间，可按块回查原文。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

__all__ = [
    "canonical",
    "same",
    "analyze",
    "CanonResult",
    "Block",
    "RuleHit",
]


# ---------------------------------------------------------------------------
# 规则编号（README 中有同一张表，改动需同步）
# ---------------------------------------------------------------------------

RULE_ATTR_SORT = "attr-sorted"            # 属性按名排序
RULE_ATTR_DROP = "attr-dropped"           # 属性不在白名单
RULE_ATTR_BOOL = "attr-boolean"           # 布尔属性去值
RULE_CLASS_SORT = "class-sorted"          # class 去重排序
RULE_WS = "whitespace-normalized"         # 空白折叠 / 纯空白文本丢弃
RULE_VOID = "void-normalized"             # 自闭合斜杠统一去除
RULE_DROP_SCRIPT = "drop-script"          # 剔除 script
RULE_DROP_STYLE = "drop-style"            # 剔除 style
RULE_DROP_COMMENT = "drop-comment"        # 剔除注释
RULE_DROP_DOCTYPE = "drop-doctype"        # 剔除 doctype / 声明 / 处理指令
RULE_DROP_HIDDEN = "drop-hidden"          # 剔除隐藏节点

# 标签名统一小写由 HTMLParser 内置完成（规则 tag-lowercase，仅文档说明）。
RULE_TAG_LOWER = "tag-lowercase"

# 全局属性白名单：任何标签都允许保留这些属性。
GLOBAL_ATTRS = frozenset(
    {
        "id",
        "class",
        "title",
        "lang",
        "dir",
        "tabindex",
        "role",
        "aria-label",
        "aria-labelledby",
        "aria-describedby",
        "aria-hidden",
        "name",
        "value",
        "href",
        "src",
        "alt",
        "action",
        "method",
        "type",
        "placeholder",
        "for",
        "colspan",
        "rowspan",
        "datetime",
    }
)

# 标签专属白名单（与全局白名单取并集）。
TAG_ATTRS = {
    "a": {"href", "title", "name"},
    "img": {"src", "alt", "width", "height"},
    "input": {"type", "name", "value", "placeholder", "checked", "disabled"},
    "button": {"type", "name", "value", "disabled"},
    "form": {"action", "method", "name"},
    "label": {"for"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "time": {"datetime"},
    "meta": {"name", "content", "charset"},
    "link": {"rel", "href"},
    "table": {"summary"},
    "textarea": {"name", "rows", "cols", "placeholder", "disabled"},
    "select": {"name", "multiple", "disabled"},
    "option": {"value", "selected", "disabled"},
}

# 布尔属性：序列化时只保留属性名，不保留值。
BOOLEAN_ATTRS = frozenset(
    {"checked", "disabled", "selected", "multiple", "readonly", "required", "hidden"}
)

# HTML 空元素（void elements）：统一不写自闭合斜杠。
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# 块级标签：块指纹的切分边界。
BLOCK_TAGS = frozenset(
    {
        "html",
        "head",
        "body",
        "header",
        "footer",
        "nav",
        "main",
        "section",
        "article",
        "aside",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "div",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "form",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "hr",
        "dl",
        "dt",
        "dd",
        "fieldset",
        "address",
        "noscript",
        "td",
        "th",
        "caption",
    }
)

# 整节点剔除的标签与对应规则。
_DROP_TAGS = {"script": RULE_DROP_SCRIPT, "style": RULE_DROP_STYLE}


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------

@dataclass
class RuleHit:
    rule: str
    tag: str | None
    attr: str | None
    detail: str
    start: int
    end: int


@dataclass
class Block:
    index: int
    tag: str                 # 块级标签名；匿名块为 "#text"
    text: str                # 该块的规范化文本
    fingerprint: str         # 块指纹（SHA256 hex）
    start: int               # 原文起始偏移
    end: int                 # 原文结束偏移（不含）


@dataclass
class CanonResult:
    canonical: str
    fingerprint: str         # 合并后的文档指纹（SHA256 hex）
    equivalence_class: str   # 等价类编号（文档指纹前 12 位）
    rules: list = field(default_factory=list)    # list[RuleHit]
    blocks: list = field(default_factory=list)   # list[Block]
    rule_counts: dict = field(default_factory=dict)

    def source_of(self, index):
        """按块下标回查原文片段的字符区间 (start, end)。"""
        block = self.blocks[index]
        return block.start, block.end

    def explain(self):
        """人类可读的等价性说明：规则命中 + 每块指纹与原文位置。"""
        lines = [f"等价类 {self.equivalence_class}  指纹 {self.fingerprint}"]
        for hit in self.rules:
            target = hit.tag or ""
            if hit.attr:
                target += f"@{hit.attr}"
            lines.append(
                f"  [{hit.rule}] {target} {hit.detail} @{hit.start}:{hit.end}"
            )
        for block in self.blocks:
            lines.append(
                f"  block#{block.index} <{block.tag}> {block.fingerprint[:12]} "
                f"原文@{block.start}:{block.end} {block.text[:40]!r}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# DOM 节点
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """所有 DOM 节点的基类，记录原文字符区间以便回查。"""

    start: int = -1
    end: int = -1


@dataclass
class Text(Node):
    text: str = ""


@dataclass
class Element(Node):
    tag: str = ""
    attrs: list = field(default_factory=list)  # [(name, value)]，保留解析原样
    children: list = field(default_factory=list)
    self_closing: bool = False


@dataclass
class Dropped(Node):
    """注释 / doctype / 声明 / 处理指令：不进规范化文本，只留规则命中。"""

    kind: str = ""  # comment | doctype | declaration | pi
    content: str = ""


# ---------------------------------------------------------------------------
# 解析器：HTML -> 自建 DOM
# ---------------------------------------------------------------------------

class _DOMBuilder(HTMLParser):
    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self._data = data
        self._line_starts = self._compute_line_starts(data)
        self.root = Element(tag="#root", start=0, end=len(data))
        self.stack = [self.root]
        self.hits = []  # 解析期能确定的剔除（注释/doctype 等）

    @staticmethod
    def _compute_line_starts(data):
        starts = [0]
        for i, ch in enumerate(data):
            if ch == "\n":
                starts.append(i + 1)
        return starts

    def _offset(self, line, col):
        """HTMLParser 的 (行, 列) 转绝对字符偏移。"""
        if line <= 0:
            return 0
        if line - 1 < len(self._line_starts):
            return self._line_starts[line - 1] + col
        return 0

    def _tag_end_offset(self, start_offset):
        """从起始偏移找到本标签 '>' 的位置（含）。"""
        gt = self._data.find(">", start_offset)
        return gt + 1 if gt != -1 else start_offset

    # --- 元素 -------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        line, col = self.getpos()
        start = self._offset(line, col)
        elem = Element(
            tag=tag,
            attrs=list(attrs),
            start=start,
            end=self._tag_end_offset(start),
        )
        self.stack[-1].children.append(elem)
        if tag not in VOID_TAGS:
            self.stack.append(elem)

    def handle_startendtag(self, tag, attrs):
        line, col = self.getpos()
        start = self._offset(line, col)
        elem = Element(
            tag=tag,
            attrs=list(attrs),
            start=start,
            end=self._tag_end_offset(start),
            self_closing=True,
        )
        self.stack[-1].children.append(elem)

    def handle_endtag(self, tag):
        line, col = self.getpos()
        start = self._offset(line, col)
        # 找到栈上最近的同名开标签，弹到它为止；容忍未匹配的闭合标签。
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                end = self._tag_end_offset(start)
                for elem in reversed(self.stack[i:]):
                    elem.end = end
                del self.stack[i:]
                return

    # --- 文本 -------------------------------------------------------------

    def handle_data(self, data):
        if not data:
            return
        line, col = self.getpos()
        start = self._offset(line, col)
        text = _collapse_ws(data)
        # 与上一个相邻文本节点合并（实体解码可能产生多段）。
        prev = self.stack[-1].children[-1] if self.stack[-1].children else None
        if isinstance(prev, Text):
            prev.text = _collapse_ws(prev.text + text)
            prev.end = start + len(data)
        else:
            self.stack[-1].children.append(
                Text(text=text, start=start, end=start + len(data))
            )

    # --- 注释 / 声明 ------------------------------------------------------

    def handle_comment(self, data):
        line, col = self.getpos()
        start = self._offset(line, col)
        node = Dropped(
            kind="comment",
            content=data,
            start=start,
            end=self._tag_end_offset(start),
        )
        self.stack[-1].children.append(node)
        self.hits.append(
            RuleHit(RULE_DROP_COMMENT, "!--", None, "注释不参与规范化", node.start, node.end)
        )

    def handle_decl(self, decl):
        line, col = self.getpos()
        start = self._offset(line, col)
        end = self._tag_end_offset(start)
        self.hits.append(
            RuleHit(RULE_DROP_DOCTYPE, "!declaration", None, "声明不参与规范化", start, end)
        )

    def unknown_decl(self, data):
        line, col = self.getpos()
        start = self._offset(line, col)
        end = self._tag_end_offset(start)
        kind = "doctype" if data.upper().startswith("DOCTYPE") else "declaration"
        self.hits.append(
            RuleHit(RULE_DROP_DOCTYPE, "!" + kind, None, "声明不参与规范化", start, end)
        )

    def handle_pi(self, data):
        line, col = self.getpos()
        start = self._offset(line, col)
        end = self._tag_end_offset(start)
        self.hits.append(
            RuleHit(RULE_DROP_DOCTYPE, "?pi", None, "处理指令不参与规范化", start, end)
        )

    def close(self):
        super().close()
        end = len(self._data)
        for elem in self.stack[1:]:
            if elem.end < 0:
                elem.end = end
        self.root.end = end


# ---------------------------------------------------------------------------
# 字符串处理
# ---------------------------------------------------------------------------

def _collapse_ws(value):
    """HTML 空白（制表/换行/回车/进纸/空格）连续序列折叠为单个空格。

    只处理 HTML 规范定义的 ASCII 空白，NBSP(\\xa0)、全角空格(\\u3000) 等
    属于普通字符，不能被 str.split() 误吞。不去除首尾空格——首尾剥离交给
    块级元素序列化阶段，这样行内元素两侧的词边界空格能保留下来。
    """
    return _WS_RE.sub(" ", value)


_WS_RE = re.compile(r"[\t\n\r\f ]+")


def _escape_text(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value):
    return _escape_text(value).replace('"', "&quot;")


def _is_hidden(elem):
    """隐藏节点判定，返回 (是否隐藏, 原因)。"""
    attrs = {name: (value or "") for name, value in elem.attrs}
    if "hidden" in attrs:
        return True, "带 hidden 属性"
    if elem.tag == "input" and attrs.get("type", "").lower() == "hidden":
        return True, "input type=hidden"
    if attrs.get("aria-hidden", "").lower().strip() == "true":
        return True, "aria-hidden=true"
    style = attrs.get("style", "").lower().replace(" ", "")
    if "display:none" in style or "visibility:hidden" in style:
        return True, "style 隐藏"
    return False, ""


def _allowed_attrs(tag):
    return GLOBAL_ATTRS | TAG_ATTRS.get(tag, frozenset())


# ---------------------------------------------------------------------------
# 规范化变换：DOM -> 规范化 DOM，同时采集规则命中
# ---------------------------------------------------------------------------

def _transform(node, hits):
    """返回节点（保留）或 None（剔除），就地重建 children。"""
    if isinstance(node, Text):
        # 仅折叠，不丢弃：纯空白节点可能是行内元素之间的词边界，
        # 首尾剥离统一在块级元素序列化（_render）时处理。
        node.text = _collapse_ws(node.text)
        return node

    if isinstance(node, Dropped):
        return None  # 解析期已记录命中

    if node.tag == "#root":
        node.children = _transform_children(node.children, hits)
        return node

    # 1) 整节点剔除：script / style / 隐藏节点
    if node.tag in _DROP_TAGS:
        hits.append(
            RuleHit(_DROP_TAGS[node.tag], node.tag, None, "节点整体剔除", node.start, node.end)
        )
        return None
    hidden, reason = _is_hidden(node)
    if hidden:
        hits.append(RuleHit(RULE_DROP_HIDDEN, node.tag, None, reason, node.start, node.end))
        return None

    # 2) 属性白名单过滤与规范化
    kept = []
    allowed = _allowed_attrs(node.tag)
    for name, value in node.attrs:
        if name not in allowed:
            hits.append(
                RuleHit(RULE_ATTR_DROP, node.tag, name, "属性不在白名单", node.start, node.end)
            )
            continue
        value = value or ""
        if name in BOOLEAN_ATTRS:
            if value != "":
                hits.append(
                    RuleHit(RULE_ATTR_BOOL, node.tag, name, "布尔属性去值", node.start, node.end)
                )
            kept.append((name, ""))
        elif name == "class":
            classes = sorted(set(_WS_RE.sub(" ", value).split()))
            new_value = " ".join(classes)
            if new_value != value:
                hits.append(
                    RuleHit(RULE_CLASS_SORT, node.tag, name, "class 去重并排序", node.start, node.end)
                )
            kept.append((name, new_value))
        else:
            # 属性值：内部连续空白折叠为单空格，首尾空白剥离。
            new_value = _WS_RE.sub(" ", value).strip()
            if new_value != value:
                hits.append(
                    RuleHit(RULE_WS, node.tag, name, "属性值空白折叠", node.start, node.end)
                )
            kept.append((name, new_value))

    sorted_attrs = sorted(kept, key=lambda item: item[0])
    if kept and [name for name, _ in sorted_attrs] != [name for name, _ in kept]:
        hits.append(RuleHit(RULE_ATTR_SORT, node.tag, None, "属性按名排序", node.start, node.end))
    node.attrs = sorted_attrs

    if node.self_closing:
        # 无论空元素还是普通元素，统一不写自闭合斜杠。
        hits.append(RuleHit(RULE_VOID, node.tag, None, "自闭合斜杠统一去除", node.start, node.end))
        node.self_closing = False

    # 3) 递归处理子节点
    node.children = _transform_children(node.children, hits)
    return node


def _transform_children(children, hits):
    result = []
    for child in children:
        kept = _transform(child, hits)
        if kept is not None:
            result.append(kept)
    return result


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def _render_attrs(attrs):
    parts = []
    for name, value in attrs:
        if value == "":
            parts.append(name)
        else:
            parts.append(f'{name}="{_escape_attr(value)}"')
    return (" " + " ".join(parts)) if parts else ""


def _render_children(elem):
    """渲染子节点；块级子元素两侧的边界空白剥离，行内词边界空格保留。"""
    cleaned = []
    for child in elem.children:
        piece = _render(child)
        if isinstance(child, Element) and child.tag in BLOCK_TAGS:
            if cleaned and cleaned[-1].endswith(" "):
                cleaned[-1] = cleaned[-1].rstrip()
            piece = piece.lstrip()
        cleaned.append(piece)
    return "".join(cleaned)


def _render(node):
    """把节点渲染成规范化字符串（紧凑、无多余空白）。"""
    if isinstance(node, Text):
        return _escape_text(node.text)
    if isinstance(node, Dropped):
        return ""
    if not isinstance(node, Element):
        return ""

    if node.tag == "#root":
        return _render_children(node).strip()

    open_tag = f"<{node.tag}{_render_attrs(node.attrs)}>"
    if node.tag in VOID_TAGS:
        return open_tag
    inner = _render_children(node)
    # 块级元素自身再去掉首尾空白。
    if node.tag in BLOCK_TAGS:
        inner = inner.strip()
    return f"{open_tag}{inner}</{node.tag}>"


# ---------------------------------------------------------------------------
# 块切分与指纹
# ---------------------------------------------------------------------------

# 结构性骨架标签：承载结构但本身不产生内容块（向子节点递归切块）。
SKELETON_TAGS = frozenset({"#root", "html", "head", "body"})


def _partition_blocks(root):
    """递归收集内容块。

    骨架节点（#root/html/head/body）不产生块，继续向子节点递归；
    块级元素各自成一个内容块（块内即使还有嵌套块，也整体作为该块文本）；
    相邻的行内/文本内容并入匿名块（tag="#text"）。
    """
    blocks = []
    inline_buffer = []

    def flush():
        if not inline_buffer:
            return
        text = "".join(_render(node) for node in inline_buffer).strip()
        if text:
            blocks.append(
                {
                    "tag": "#text",
                    "text": text,
                    "start": inline_buffer[0].start,
                    "end": inline_buffer[-1].end,
                }
            )
        inline_buffer.clear()

    def walk(node):
        if isinstance(node, Text):
            inline_buffer.append(node)
            return
        if isinstance(node, Dropped) or not isinstance(node, Element):
            return
        if node.tag in SKELETON_TAGS:
            for child in node.children:
                walk(child)
            return
        if node.tag in BLOCK_TAGS:
            flush()
            blocks.append(
                {"tag": node.tag, "text": _render(node), "start": node.start, "end": node.end}
            )
            return
        for child in node.children:  # 行内容器：继续向里找
            walk(child)

    walk(root)
    flush()
    return blocks


def _fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def analyze(html):
    """规范化并返回完整分析结果（CanonResult）。"""
    data = html or ""
    builder = _DOMBuilder(data)
    builder.feed(data)
    builder.close()

    hits = list(builder.hits)
    root = _transform(builder.root, hits)

    canonical_text = _render(root)

    raw_blocks = _partition_blocks(root)
    blocks = []
    block_hashes = []
    for index, raw in enumerate(raw_blocks):
        digest = _fingerprint(raw["text"])
        block_hashes.append(digest)
        blocks.append(
            Block(
                index=index,
                tag=raw["tag"],
                text=raw["text"],
                fingerprint=digest,
                start=raw["start"],
                end=raw["end"],
            )
        )

    # 合并：各块指纹按文档顺序逐行拼接，再做一次 SHA256。
    # 无内容块时拼接结果为空串，文档指纹即 sha256("")，仍是确定值。
    merged_input = "\n".join(block_hashes).encode("utf-8")
    doc_fingerprint = hashlib.sha256(merged_input).hexdigest()

    counts = {}
    for hit in hits:
        counts[hit.rule] = counts.get(hit.rule, 0) + 1

    return CanonResult(
        canonical=canonical_text,
        fingerprint=doc_fingerprint,
        equivalence_class=doc_fingerprint[:12],
        rules=hits,
        blocks=blocks,
        rule_counts=counts,
    )


def canonical(html):
    """返回规范化文本（接口与旧版一致）。"""
    return analyze(html).canonical


def same(a, b):
    """两个 HTML 是否属于同一等价类：比较合并后的块指纹。"""
    return analyze(a).fingerprint == analyze(b).fingerprint
