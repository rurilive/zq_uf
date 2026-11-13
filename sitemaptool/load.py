"""sitemap 入口发现：urlset / sitemap index 解析、gz 流式解压、索引递归展开。

仅依赖 Python 标准库。单个来源解析失败只记录样本并跳过，不影响其他来源。
"""
from __future__ import annotations

import contextlib
import gzip
import io
import logging
import os
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from urllib.parse import urljoin

logger = logging.getLogger("sitemaptool")

MAX_INDEX_DEPTH = 10
_SAMPLE_LIMIT = 200
_GZIP_MAGIC = b"\x1f\x8b"
_URL_FIELDS = ("loc", "lastmod", "changefreq")
_INDEX_FIELDS = ("loc", "lastmod")


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def open_stream(source, timeout=10):
    """打开本地路径或 http(s)/file URL，按 .gz 后缀或魔数流式解压。

    解压基于 :class:`gzip.GzipFile` 分块进行，不会把压缩包整包读入内存。
    """
    raw, name = _open_raw(source, timeout)
    if _looks_gzipped(raw, name):
        return _ClosingGzipFile(raw)
    return raw


class _ClosingGzipFile(gzip.GzipFile):
    """关闭解压流时一并关闭底层文件对象。"""

    def __init__(self, raw):
        super().__init__(fileobj=raw)
        self._raw = raw

    def close(self):
        try:
            super().close()
        finally:
            self._raw.close()


def _open_raw(source, timeout=10):
    if hasattr(source, "read"):
        return source, getattr(source, "name", "") or ""
    if isinstance(source, str) and source.startswith(
        ("http://", "https://", "file://")
    ):
        return urllib.request.urlopen(source, timeout=timeout), source
    return open(source, "rb"), source


def _looks_gzipped(stream, name):
    if name.endswith(".gz"):
        return True
    try:
        return stream.peek(2)[:2] == _GZIP_MAGIC
    except (AttributeError, OSError, ValueError):
        return False


def parse_stream(stream):
    """流式解析 urlset 或 sitemap index，返回 ``(kind, items)``。"""
    root = None
    kind = None
    items = []
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        name = _localname(elem.tag)
        if event == "start":
            if root is None:
                root = elem
                kind = "index" if name == "sitemapindex" else "urlset"
            continue
        if kind == "urlset" and name == "url":
            items.append(_read_children(elem, _URL_FIELDS))
        elif kind == "index" and name == "sitemap":
            items.append(_read_children(elem, _INDEX_FIELDS))
        if name in ("url", "sitemap"):
            elem.clear()
    if kind is None:
        raise ET.ParseError("缺少 urlset / sitemapindex 根节点")
    if root is not None:
        root.clear()
    return kind, items


def _read_children(elem, fields):
    item = {field: "" for field in fields}
    for child in elem:
        name = _localname(child.tag)
        if name in item and child.text is not None:
            item[name] = child.text.strip()
    return item


def parse_urlset(xml_text):
    """解析 urlset，兼容字符串、bytes 与二进制流（含 gzip 流）。"""
    if isinstance(xml_text, str):
        stream = io.BytesIO(xml_text.encode("utf-8"))
    elif isinstance(xml_text, (bytes, bytearray)):
        stream = io.BytesIO(bytes(xml_text))
    else:
        stream = xml_text
    kind, items = parse_stream(stream)
    if kind != "urlset":
        raise ET.ParseError("不是 urlset 文件")
    return items


def resolve_location(base, ref):
    """把索引中的相对 loc 解析成绝对 URL 或本地路径。"""
    if base and "://" in base:
        return urljoin(base, ref)
    if "://" in ref or os.path.isabs(ref):
        return ref
    return os.path.normpath(os.path.join(os.path.dirname(base or ""), ref))


def iter_source_entries(sources):
    """广度优先递归展开 sitemap index，逐条产出 urlset entry。"""
    queue = deque((source, 0) for source in sources)
    seen = set()
    while queue:
        source, depth = queue.popleft()
        if source in seen:
            continue
        seen.add(source)
        try:
            with contextlib.closing(open_stream(source)) as stream:
                kind, items = parse_stream(stream)
        except Exception as exc:
            logger.warning(
                "来源解析失败，已跳过：%s（%s）样本=%r",
                source,
                exc,
                _failure_sample(source, exc),
            )
            continue
        if kind != "index":
            for entry in items:
                yield entry
            continue
        if depth >= MAX_INDEX_DEPTH:
            logger.warning(
                "索引递归深度超过 %s，停止展开：%s", MAX_INDEX_DEPTH, source
            )
            continue
        for item in items:
            child = resolve_location(source, item["loc"])
            if child and child not in seen:
                queue.append((child, depth + 1))


def _failure_sample(source, exc):
    try:
        if isinstance(source, str) and os.path.isfile(source):
            with open(source, "rb") as handle:
                return handle.read(_SAMPLE_LIMIT).decode("utf-8", "replace")
    except OSError:
        pass
    return str(exc)[:_SAMPLE_LIMIT]


def load_sources(paths):
    """入口保持不变：递归发现所有 urlset 链接，返回扁平 entry 列表。"""
    return list(iter_source_entries(list(paths)))
