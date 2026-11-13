"""HTML 链接解析、分页推断与详情页链接提取（仅用标准库）。"""

import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlparse

PAGE_PARAM_HINTS = ("page", "pageno", "pagenum", "page_num", "pn", "p")
NON_PAGE_PARAMS = frozenset(("sign", "signature", "sig", "token", "_ts", "timestamp", "nonce",
                             "appkey", "app_key", "id", "uid", "callback"))
NEXT_TEXT = ("下一页", "下页", "›", "»", "next", "older", "more")
DETAIL_HINTS = ("detail", "item", "article", "news", "product", "show", "view",
                "content", "goods", "post", "doc", "info", "/p/", "/d/")
_TRAILING_PAGE = re.compile(r"^(.*?)(\d+)(\.html?)?$")
_DIGITS = re.compile(r"\d+")


class Anchor:
    def __init__(self, href, text, classes):
        self.href = href
        self.text = text
        self.classes = classes


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self._href = None
        self._classes = ""
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        href = attrs.get("href")
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            return
        self._href = href.strip()
        self._classes = attrs.get("class", "").strip()
        self._chunks = []

    def handle_data(self, data):
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.anchors.append(Anchor(self._href, "".join(self._chunks).strip(), self._classes))
            self._href = None
            self._chunks = []


def collect_anchors(page_url, html_text):
    collector = _AnchorCollector()
    collector.feed(html_text or "")
    result = []
    for anchor in collector.anchors:
        absolute = urljoin(page_url, anchor.href)
        result.append(Anchor(absolute, anchor.text, anchor.classes))
    return result


def infer_pagination(list_pages):
    """list_pages: [{"url", "anchors": [Anchor]}]，汇总推断分页规则。"""
    param_counter = Counter()
    path_counter = Counter()
    path_prefixes = {}
    numeric_text_values = []
    next_link = None
    evidence = []

    for page in list_pages:
        current = urlparse(page["url"])
        for anchor in page["anchors"]:
            parsed = urlparse(anchor.href)
            if parsed.netloc != current.netloc:
                continue
            text = anchor.text.lower()
            if text.isdigit():
                numeric_text_values.append(int(text))
            if next_link is None and any(word in text for word in NEXT_TEXT):
                next_link = anchor
            for key, value in parse_qsl(parsed.query):
                if value.isdigit() and int(value) > 0 and key.lower() not in NON_PAGE_PARAMS:
                    param_counter[key.lower()] += 1
            if parsed.path != current.path:
                match = _TRAILING_PAGE.match(parsed.path)
                if match and match.group(2) and _same_directory(current.path, parsed.path) \
                        and _same_page_prefix(current.path, parsed.path):
                    pattern = match.group(1) + "{page}" + (match.group(3) or "")
                    path_counter[pattern] += 1
                    path_prefixes[pattern] = match.group(1)

    page_param = _choose_param(param_counter)
    path_pattern = path_counter.most_common(1)[0][0] if path_counter else None

    if page_param:
        style, selector = "query", 'a[href*="%s="]' % page_param
        evidence.append("查询参数 %s 在 %d 个分页链接中出现" % (page_param, param_counter[page_param]))
    elif path_pattern:
        style, selector = "path", 'a[href*="%s"]' % path_prefixes[path_pattern]
        evidence.append("路径式分页模式 %s 出现 %d 次" % (path_pattern, path_counter[path_pattern]))
    else:
        style, selector = "none", None

    if next_link is not None:
        if next_link.classes:
            selector = "a.%s" % next_link.classes.split()[0]
        evidence.append("下一页链接文本为 %r，指向 %s" % (next_link.text, next_link.href))
    elif style != "none":
        evidence.append("未发现显式下一页链接，按分页模式构造选择器")

    page_path_prefix = path_prefixes[path_pattern] if path_pattern else None
    total_pages = _total_pages(list_pages, page_param, page_path_prefix, numeric_text_values)
    if total_pages:
        evidence.append("链接中观测到的最大页码为 %d" % total_pages)

    return {
        "style": style,
        "page_param": page_param,
        "path_pattern": path_pattern,
        "next_selector": selector,
        "next_href": next_link.href if next_link else None,
        "total_pages": total_pages,
        "evidence": evidence,
    }


def extract_detail_links(site_url, list_pages, pagination, limit=3):
    """从列表页提取同站详情链接，并归纳详情 URL 模式。"""
    site_host = urlparse(site_url).netloc
    page_param = pagination.get("page_param")
    path_pattern = pagination.get("path_pattern")
    path_prefix = path_pattern.split("{page}")[0] if path_pattern else None
    seen = set()
    pattern_counter = Counter()
    ordered = []

    for page in list_pages:
        for anchor in page["anchors"]:
            parsed = urlparse(anchor.href)
            if parsed.netloc != site_host or anchor.href in seen:
                continue
            if _is_pagination_href(parsed, page["url"], page_param, path_pattern):
                continue
            if not _looks_like_detail(parsed, anchor.text):
                continue
            seen.add(anchor.href)
            ordered.append(anchor.href)
            pattern_counter[_detail_pattern(parsed.path)] += 1

    pattern = pattern_counter.most_common(1)[0][0] if pattern_counter else None
    return {"detail_pattern": pattern, "urls": ordered[:limit], "candidates": len(ordered)}


def _choose_param(param_counter):
    if not param_counter:
        return None
    best = max(
        param_counter,
        key=lambda key: (param_counter[key] * 10 + _hint_score(key)),
    )
    return best


def _hint_score(key):
    lowered = key.lower()
    for index, hint in enumerate(PAGE_PARAM_HINTS):
        if lowered == hint:
            return len(PAGE_PARAM_HINTS) - index
    return 0


def _same_directory(base_path, candidate_path):
    """分页路径与当前列表页需位于同一目录，避免把详情链接误判成分页。"""
    return base_path.rsplit("/", 1)[0] == candidate_path.rsplit("/", 1)[0]


def _same_page_prefix(base_path, candidate_path):
    """分页文件名与列表页共享非数字前缀，如 list_1.html -> list_2.html。"""
    base_name = base_path.rsplit("/", 1)[-1]
    candidate_name = candidate_path.rsplit("/", 1)[-1]
    base_prefix = _DIGITS.split(base_name)[0]
    candidate_prefix = _DIGITS.split(candidate_name)[0]
    return bool(base_prefix) and base_prefix == candidate_prefix


def _total_pages(list_pages, page_param, path_prefix, numeric_text_values):
    values = list(numeric_text_values)
    for page in list_pages:
        list_path = urlparse(page["url"]).path
        for anchor in page["anchors"]:
            parsed = urlparse(anchor.href)
            if page_param:
                for key, value in parse_qsl(parsed.query):
                    if key.lower() == page_param and value.isdigit():
                        values.append(int(value))
            if path_prefix and parsed.path != list_path and parsed.path.startswith(path_prefix):
                trailing = parsed.path[len(path_prefix):].rstrip("/")
                if trailing.isdigit():
                    values.append(int(trailing))
    values = [value for value in values if 1 <= value <= 9999]
    return max(values) if values else None


def _is_pagination_href(parsed, list_url, page_param, path_pattern):
    list_path = urlparse(list_url).path
    if page_param and any(key.lower() == page_param for key, _ in parse_qsl(parsed.query)):
        return True
    if path_pattern and parsed.path != list_path:
        regex = re.escape(path_pattern).replace(re.escape("{page}"), r"\d+")
        return re.fullmatch(regex, parsed.path) is not None
    return False


def _looks_like_detail(parsed, anchor_text):
    path = parsed.path.lower()
    if any(hint in path for hint in DETAIL_HINTS):
        return True
    if _DIGITS.search(parsed.path) and len(path.strip("/").split("/")) >= 2:
        return True
    return bool(anchor_text and _DIGITS.search(parsed.path))


def _detail_pattern(path):
    return _DIGITS.sub(lambda match: "(\\d+)", path)
