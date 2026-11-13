"""站内链接发现。

职责：从页面 HTML 中提取链接，并完成
规范化（去会话参数/锚点、同路径去重）、同站过滤、翻页归并、
深度与每层数量上限控制。纯标准库实现。
"""
import re
from urllib.parse import urlsplit, urlunsplit, urljoin, parse_qsl, urlencode

MAX_DEPTH = 5
MAX_PER_LEVEL = 100

SESSION_PARAMS = frozenset({
    "sid", "sessionid", "session_id", "phpsessid", "jsessionid",
})
SESSION_PARAM_PREFIXES = ("aspsessionid",)

PAGINATION_PARAMS = frozenset({"page", "pageno", "p", "pn"})
PAGINATION_PATH_RE = re.compile(r"/(?:page|p)/(\d+)", re.IGNORECASE)
PAGINATION_TEXTS = frozenset({
    "下一页", "上一页", "首页", "尾页", "末页", "更多",
    "next", "prev", "previous", "more",
})
PAGE_NUM_TEXT_RE = re.compile(r"第\s*\d+\s*页")

_ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class Page:
    """待发现的页面：HTML 内容、页面 URL（作为相对链接基准与来源）。"""

    def __init__(self, html, url=None, site_host=None):
        self.html = html
        self.url = url
        self.site_host = site_host


class Link(str):
    """链接字符串，附带发现元数据；与普通 str 比较行为一致。"""

    def __new__(cls, url, dedup_key, source, depth, is_pagination=False,
                parent=None):
        obj = str.__new__(cls, url)
        obj.dedup_key = dedup_key
        obj.source = source
        obj.depth = depth
        obj.is_pagination = is_pagination
        obj.parent = parent
        return obj


class DiscoverResult(list):
    """发现结果：本体是新页面链接列表，另含翻页链接与被拒原因。"""

    def __init__(self, links=None, pagination=None, rejected=None):
        super().__init__(links or [])
        self.links = list(links or [])
        self.pagination = list(pagination or [])
        self.rejected = list(rejected or [])


def _is_session_param(name):
    low = name.lower()
    if low in SESSION_PARAMS:
        return True
    return any(low.startswith(prefix) for prefix in SESSION_PARAM_PREFIXES)


def normalize_url(href, base=None):
    """规范化单个链接：补全、去锚点、去会话参数、查询参数排序。"""
    absolute = urljoin(base or "", href)
    parts = urlsplit(absolute)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_session_param(key)
    ]
    query.sort()
    path = parts.path or ("/" if parts.netloc else "")
    normalized = urlunsplit((
        parts.scheme,
        parts.netloc,
        path,
        urlencode(query),
        "",
    ))
    dedup_key = urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))
    if not parts.scheme and not parts.netloc:
        dedup_key = path.rstrip("/") or "/"
    return normalized, dedup_key, parts


def _infer_site_host(html):
    for match in _ANCHOR_RE.finditer(html):
        href_match = _HREF_RE.search(match.group(0))
        if not href_match:
            continue
        host = urlsplit(href_match.group(1)).hostname
        if host:
            return host
    return None


def _is_pagination(parts, anchor_text):
    query_keys = {key.lower() for key, _ in parse_qsl(parts.query)}
    if query_keys & PAGINATION_PARAMS:
        return True
    if PAGINATION_PATH_RE.search(parts.path):
        return True
    text = _TAG_RE.sub("", anchor_text).strip()
    if text.lower() in PAGINATION_TEXTS:
        return True
    if text.isdigit() or PAGE_NUM_TEXT_RE.search(text):
        return True
    return False


def discover(page, depth=1, *, max_depth=MAX_DEPTH,
             max_per_level=MAX_PER_LEVEL, allow_external=False):
    """发现页面内的站内链接。

    返回 :class:`DiscoverResult`：普通新页面链接在列表本体中，
    翻页链接归并到 ``pagination``（不单独计深度），被拒链接记入
    ``rejected``（含 reason/source/dedup_key）。
    """
    if isinstance(page, str):
        html, source, site_host = page, None, None
    else:
        html, source = page.html, page.url
        site_host = page.site_host

    base = source
    if site_host is None:
        site_host = _infer_site_host(html)

    links, pagination, rejected = [], [], []
    seen = set()

    def reject(url, reason, dedup_key=None):
        rejected.append({
            "url": url,
            "reason": reason,
            "source": source,
            "dedup_key": dedup_key,
        })

    for anchor in _ANCHOR_RE.finditer(html):
        tag = anchor.group(0)
        href_match = _HREF_RE.search(tag)
        if not href_match:
            continue
        href = href_match.group(1).strip()
        if not href or href.startswith("#"):
            continue

        raw_parts = urlsplit(urljoin(base or "", href))
        if raw_parts.scheme and raw_parts.scheme not in ("http", "https"):
            reject(href, "unsupported_scheme")
            continue

        url, dedup_key, parts = normalize_url(href, base)

        if not allow_external and site_host and parts.hostname and \
                parts.hostname != site_host:
            reject(url, "external", dedup_key)
            continue

        is_page_link = _is_pagination(parts, anchor.group(1))

        if dedup_key in seen:
            reject(url, "duplicate", dedup_key)
            continue

        if is_page_link:
            seen.add(dedup_key)
            pagination.append(Link(
                url, dedup_key, source, depth,
                is_pagination=True, parent=source,
            ))
            continue

        if depth >= max_depth:
            reject(url, "depth_limit", dedup_key)
            continue
        if len(links) >= max_per_level:
            reject(url, "per_level_limit", dedup_key)
            continue

        seen.add(dedup_key)
        links.append(Link(url, dedup_key, source, depth + 1))

    return DiscoverResult(links=links, pagination=pagination, rejected=rejected)
