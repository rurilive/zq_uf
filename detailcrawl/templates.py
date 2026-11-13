"""详情页按模板分组，同组共用首页面学到的字段规则。"""
from __future__ import annotations

import hashlib
import threading
from html.parser import HTMLParser


class ParseError(Exception):
    """页面字段解析失败。"""


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = []


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                break

    def handle_data(self, data):
        if data.strip():
            self._stack[-1].text.append(data)


def _parse_tree(html):
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _signature(root):
    """模板签名：文档顺序的标签骨架，忽略文本内容。"""
    tags = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node is not root:
            tags.append(node.tag)
        stack.extend(reversed(node.children))
    return hashlib.blake2s(" ".join(tags).encode("utf-8"), digest_size=6).hexdigest()


def _text_of(node):
    parts = list(node.text)
    for child in node.children:
        parts.append(_text_of(child))
    return "".join(parts).strip()


def _walk(node, path=()):
    yield node, path
    for index, child in enumerate(node.children):
        yield from _walk(child, path + (index,))


def _node_at(root, path):
    node = root
    for index in path:
        if index >= len(node.children):
            return None
        node = node.children[index]
    return node


class TemplateRules:
    """一个模板组的字段规则：字段名 -> 元素路径。"""

    def __init__(self, rules):
        self.rules = rules

    @classmethod
    def learn(cls, root):
        rules = []
        seen = set()
        for node, path in _walk(root):
            name = None
            if node.tag == "title":
                name = "title"
            elif "data-field" in node.attrs:
                name = node.attrs["data-field"]
            if name and name not in seen:
                seen.add(name)
                rules.append((name, path))
        if not rules:
            raise ParseError("no title or data-field elements found")
        return cls(rules)

    def apply(self, root):
        fields = {}
        for name, path in self.rules:
            node = _node_at(root, path)
            if node is None:
                raise ParseError(f"field {name!r} missing (template drifted)")
            text = _text_of(node)
            if not text:
                raise ParseError(f"field {name!r} is empty")
            fields[name] = text
        return fields


class TemplateRegistry:
    """按模板签名分组，同组共用一套 TemplateRules。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._rules = {}
        self._groups = {}

    def parse(self, html):
        root = _parse_tree(html)
        signature = _signature(root)
        with self._lock:
            rules = self._rules.get(signature)
            self._groups[signature] = self._groups.get(signature, 0) + 1
        if rules is None:
            rules = TemplateRules.learn(root)
            with self._lock:
                rules = self._rules.setdefault(signature, rules)
        return signature, rules.apply(root)

    @property
    def groups(self):
        with self._lock:
            return dict(self._groups)

    @property
    def rules_built(self):
        with self._lock:
            return len(self._rules)
