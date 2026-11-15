"""极简 HTML DOM，只依赖标准库，供块级抽取使用。

`html.parser` 不做纠错，这里补上常见的隐式闭合规则（p/li/td/tr 等），
保证站点改版后的畸形 HTML 也能遍历，不会直接抛异常。
"""
from html.parser import HTMLParser

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# 这些标签之间的文本需要空格分隔，避免 "第一段第二段" 粘连
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "caption", "dd",
    "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul",
}

# 纯行内标签：只含这些子元素的节点仍可视为一个文本块
INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del",
    "dfn", "em", "font", "i", "img", "ins", "kbd", "label", "mark", "q",
    "rp", "rt", "ruby", "s", "samp", "small", "span", "strike", "strong",
    "sub", "sup", "time", "u", "var", "wbr",
}

# 段落级标签：直接作为一个抽取块
UNIT_TAGS = {
    "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
    "td", "th", "dd", "dt", "figcaption", "caption", "summary", "address",
    "cite", "q", "label", "time",
}

# 列表项标签：用于识别列表页/索引页（正文块大多来自 ul/ol）
LIST_TAGS = {"li"}

# 无论站点怎么配置，这些标签的文本都不能进正文
ALWAYS_SKIP = {"script", "style", "noscript", "template", "svg"}

_P_CLOSERS = {
    "address", "article", "aside", "blockquote", "details", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hgroup", "hr", "main", "nav", "ol", "p",
    "pre", "section", "table", "ul",
}

_IMPLICIT_CLOSE = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
    "option": {"option"},
    "optgroup": {"option", "optgroup"},
}


class Node:
    """元素节点；字符串子节点表示文本。"""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent

    def elements(self):
        return [child for child in self.children if isinstance(child, Node)]

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def __repr__(self):
        return "<Node %s %r>" % (self.tag, self.attrs)


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def _open(self, tag, attrs, push):
        tag = tag.lower()
        while len(self.stack) > 1:
            top = self.stack[-1].tag
            if tag in _P_CLOSERS and top == "p":
                self.stack.pop()
                continue
            if top in _IMPLICIT_CLOSE.get(tag, ()):
                self.stack.pop()
                continue
            break
        node = Node(tag, {key.lower(): (value or "") for key, value in attrs},
                    self.stack[-1])
        self.stack[-1].children.append(node)
        if push and tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, push=True)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, push=False)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return
        # 多余/错配的结束标签直接忽略，不打断后续解析

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def parse(html):
    """把 HTML 解析成节点树，返回 #document 根节点。"""
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def text_of(node, skip=ALWAYS_SKIP):
    """节点可见文本；块级子元素之间补空格，丢弃脚本样式。

    用显式栈遍历而不是递归：站点畸形 HTML 可能嵌套上千层，递归会爆栈。
    """
    parts = []
    stack = [iter(node.children)]
    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(child, str):
            parts.append(child)
            continue
        if child.tag in skip:
            continue
        if child.tag == "br":
            parts.append(" ")
            continue
        if child.tag in BLOCK_TAGS:
            parts.append(" ")
        stack.append(iter(child.children))
    return clean_text("".join(parts))


def clean_text(text):
    """压掉多余空白，并把不换行空格当空格处理。"""
    return " ".join((text or "").replace("\u00a0", " ").split())


def is_inline_only(node):
    return all(
        child.tag in INLINE_TAGS
        for child in node.children
        if isinstance(child, Node)
    )


def locator(node, max_parts=8):
    """生成便于人工定位的简单路径，例如 article#post>div.body>p:nth-of-type(2)。

    嵌套特别深时只保留末尾几段，前缀用 … 表示，避免超长路径。
    """
    parts = []
    current = node
    while current is not None and current.parent is not None:
        segment = current.tag
        node_id = str(current.attrs.get("id", "")).strip()
        if node_id:
            segment += "#" + node_id[:24]
        else:
            siblings = [c for c in current.parent.elements() if c.tag == current.tag]
            if len(siblings) > 1:
                segment += ":nth-of-type(%d)" % (siblings.index(current) + 1)
        parts.append(segment)
        current = current.parent
    parts.reverse()
    if len(parts) > max_parts:
        parts = ["…"] + parts[-max_parts:]
    return ">".join(parts) if parts else "#document"