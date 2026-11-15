"""按深度和配额扩散种子页面的链接。

- 页面深度为 ``depth`` 时，它的链接只进入 ``depth + 1`` 层；到达 ``max_depth`` 后不再入队。
- 每个（站点, 目标深度）有一条数量上限，配额在站点内跨批累计。
- 同一批次超出配额时按链接特征得分截断：内容页 > 列表页 > 其他 > 导航页 > 媒体。
  导航链接每层单独限流，媒体链接默认不扩散。
- 跨批去重使用规范化 URL，忽略 http/https、尾斜杠、fragment 等差异。
- 每条入队目标记录来源页和进入队列时的深度。
"""

import logging
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

logger = logging.getLogger("seedexpand")

DEFAULT_MAX_DEPTH = 3
DEFAULT_QUOTAS = {1: 40, 2: 20, 3: 8}
DEFAULT_CATEGORY_CAPS = {"nav": 2, "media": 0}
# 入队速率用最近这段时间内的新增量估算。
RATE_WINDOW_SECONDS = 60.0

_CATEGORY_SCORE = {"content": 100, "list": 60, "other": 40, "nav": 20, "media": 0}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMERIC_RE = re.compile(r"^\d+$")
_NUMERIC_PATH_RE = re.compile(r"/\d+(?:/|$)")

_CONTENT_WORDS = {
    "article", "articles", "post", "posts", "detail", "details", "item",
    "items", "product", "products", "news", "story", "stories", "thread",
    "threads", "topic", "topics", "doc", "docs", "video", "videos",
    "gallery", "entry", "entries",
}
_LIST_WORDS = {
    "list", "lists", "category", "categories", "page", "pages", "index",
    "archive", "archives", "tag", "tags", "search", "catalog", "browse",
    "feed", "blog", "blogs",
}
_NAV_WORDS = {
    "about", "contact", "login", "logout", "signin", "signup", "register",
    "privacy", "terms", "faq", "help", "home", "nav", "menu", "sitemap",
}
_MEDIA_SUFFIXES = {
    "jpg", "jpeg", "png", "gif", "svg", "webp", "ico", "bmp",
    "css", "js", "mp4", "avi", "mov", "mp3", "wav", "zip", "rar",
    "7z", "exe", "dmg", "apk",
}
_CONTENT_PARAMS = {
    "id", "item", "item_id", "article", "article_id", "post", "post_id",
    "product", "product_id", "topic", "topic_id",
}
_PAGE_PARAMS = {"page", "p", "paged", "start", "offset"}

# site -> 扩散状态（见 _state）
_SITES = {}


def _state(site):
    st = _SITES.get(site)
    if st is None:
        st = {
            "quotas": dict(DEFAULT_QUOTAS),
            "max_depth": DEFAULT_MAX_DEPTH,
            "category_caps": dict(DEFAULT_CATEGORY_CAPS),
            "seen": {},
            "used": {},
            "cat_used": {},
            "first_ts": None,
            "enqueued_at": [],
            "rate": 0.0,
        }
        _SITES[site] = st
    return st


def reset(site=None):
    """清空扩散状态；不传 site 时清空全部站点。"""
    if site is None:
        _SITES.clear()
    else:
        _SITES.pop(str(site), None)


def configure(site, quotas=None, max_depth=None, category_caps=None):
    """覆盖站点的配额、深度上限或分类上限，返回新的 budget 快照。"""
    st = _state(str(site))
    if quotas:
        for depth, limit in quotas.items():
            st["quotas"][int(depth)] = int(limit)
    if max_depth is not None:
        st["max_depth"] = int(max_depth)
    if category_caps:
        for category, cap in category_caps.items():
            st["category_caps"][str(category)] = int(cap)
    return budget(site)


def canonical_url(url):
    """返回用于跨批去重的规范化 URL。

    http/https 视为同一目标；忽略主机大小写、默认端口、重复斜杠、
    尾斜杠和 fragment。查询串保持原样。
    """
    raw = str(url or "").strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return raw
    scheme = "http"
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port not in (80, 443):
        netloc = "%s:%d" % (host, port)
    else:
        netloc = host
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = path.rstrip("/") or "/"
    base = "%s://%s%s" % (scheme, netloc, path)
    return base + ("?" + parts.query if parts.query else "")


def _classify(url):
    """按链接特征返回 (分类, 优先级得分)。"""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme and scheme not in ("http", "https"):
        return "media", _CATEGORY_SCORE["media"]
    path = (parts.path or "/").lower()
    tokens = set(_TOKEN_RE.findall(path))
    last = path.rsplit("/", 1)[-1]
    suffix = last.rsplit(".", 1)[1] if "." in last else ""
    if suffix in _MEDIA_SUFFIXES:
        return "media", _CATEGORY_SCORE["media"]

    params = {
        key.lower(): values
        for key, values in parse_qs(parts.query, keep_blank_values=True).items()
    }

    if tokens & _CONTENT_WORDS:
        category, score = "content", _CATEGORY_SCORE["content"]
    elif tokens & _LIST_WORDS:
        category, score = "list", _CATEGORY_SCORE["list"]
    elif tokens & _NAV_WORDS:
        category, score = "nav", _CATEGORY_SCORE["nav"]
    else:
        category, score = "other", _CATEGORY_SCORE["other"]

    if any(key in _CONTENT_PARAMS for key in params):
        category, score = "content", _CATEGORY_SCORE["content"] + 5
    elif category != "content" and any(
        key in _PAGE_PARAMS and any(_NUMERIC_RE.match(value) for value in values)
        for key, values in params.items()
    ):
        category, score = "list", _CATEGORY_SCORE["list"] + 5

    if _NUMERIC_PATH_RE.search(path) and category in ("content", "other"):
        score += 10
    return category, score


def score_link(url):
    """返回链接的扩散优先级得分，分数越高越优先保留。"""
    return _classify(str(url or ""))[1]


def _site_of(page):
    explicit = page.get("site")
    if explicit:
        return str(explicit)
    url = str(page.get("url") or "")
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return host or "unknown"


def _resolve(raw, base):
    text = str(raw or "").strip()
    if not text:
        return ""
    return urljoin(base, text) if base else text


def expand(page, depth):
    """展开深度为 ``depth`` 的页面，返回新增、截断、重复和深度分布。"""
    if not isinstance(page, dict):
        raise TypeError("page must be a dict")
    depth = int(depth)
    source = str(page.get("url") or page.get("source") or "")
    site = _site_of(page)
    st = _state(site)
    target_depth = depth + 1
    links = list(page.get("links", []))

    boundary = target_depth > st["max_depth"]
    limit = 0 if boundary else st["quotas"].get(target_depth, 0)
    used_before = st["used"].get(target_depth, 0)
    remaining = max(0, limit - used_before)
    cat_used = st["cat_used"].setdefault(target_depth, {})

    added = []
    added_detail = []
    truncated = []
    duplicates = []
    candidates = []
    batch_keys = set()

    for index, raw in enumerate(links):
        url = _resolve(raw, source)
        if not url:
            continue
        key = canonical_url(url)
        if key in st["seen"]:
            record = st["seen"][key]
            duplicates.append({
                "url": url,
                "canonical": key,
                "reason": "already-enqueued",
                "first_source": record["source"],
                "depth": record["depth"],
            })
            continue
        if key in batch_keys:
            duplicates.append({
                "url": url,
                "canonical": key,
                "reason": "duplicate-in-batch",
                "first_source": source,
                "depth": target_depth,
            })
            continue
        batch_keys.add(key)
        category, score = _classify(url)
        candidates.append({
            "index": index,
            "url": url,
            "canonical": key,
            "category": category,
            "score": score,
        })

    ordered = sorted(candidates, key=lambda cand: (-cand["score"], cand["index"]))

    for cand in ordered:
        category = cand["category"]
        cap = st["category_caps"].get(category, limit)
        reason = None
        if boundary:
            reason = "max-depth"
        elif category == "media" and cap <= 0:
            reason = "media"
        elif remaining <= 0:
            reason = "depth-quota"
        elif cat_used.get(category, 0) >= cap:
            reason = "category-cap"
        if reason is not None:
            truncated.append({
                "url": cand["url"],
                "canonical": cand["canonical"],
                "category": category,
                "score": cand["score"],
                "reason": reason,
                "depth": target_depth,
            })
            continue

        remaining -= 1
        st["used"][target_depth] = st["used"].get(target_depth, 0) + 1
        cat_used[category] = cat_used.get(category, 0) + 1
        st["seen"][cand["canonical"]] = {
            "url": cand["url"],
            "source": source,
            "depth": target_depth,
            "score": cand["score"],
            "category": category,
        }
        added.append(cand["url"])
        added_detail.append({
            "url": cand["url"],
            "canonical": cand["canonical"],
            "category": category,
            "score": cand["score"],
            "depth": target_depth,
            "source": source,
        })

    now = time.monotonic()
    st["first_ts"] = now if st["first_ts"] is None else st["first_ts"]
    st["enqueued_at"].extend([now] * len(added))
    window_start = now - RATE_WINDOW_SECONDS
    st["enqueued_at"] = [ts for ts in st["enqueued_at"] if ts >= window_start]
    elapsed = min(RATE_WINDOW_SECONDS, max(now - st["first_ts"], 1.0))
    rate = len(st["enqueued_at"]) / elapsed
    st["rate"] = rate

    queue_depth = len(st["seen"])
    depth_counts = {d: 0 for d in range(1, st["max_depth"] + 1)}
    for record in st["seen"].values():
        depth_counts[record["depth"]] = depth_counts.get(record["depth"], 0) + 1

    logger.info(
        "expand site=%s source=%s depth=%d target_depth=%d added=%d "
        "truncated=%d duplicates=%d queue_depth=%d enqueue_rate=%.2f/s",
        site, source, depth, target_depth, len(added),
        len(truncated), len(duplicates), queue_depth, rate,
    )

    return {
        "depth": depth,
        "added": added,
        "added_detail": added_detail,
        "truncated": truncated,
        "duplicates": duplicates,
        "depth_counts": depth_counts,
        "queue_depth": queue_depth,
        "enqueue_rate": rate,
        "layer_budget": {
            "depth": target_depth,
            "limit": limit,
            "used": st["used"].get(target_depth, 0),
            "remaining": max(0, limit - st["used"].get(target_depth, 0)),
        },
    }


def budget(site):
    """返回站点扩散预算快照：站点总上限、逐层用量和已入队目标。"""
    site = str(site)
    st = _state(site)
    layers = {}
    for depth in range(1, st["max_depth"] + 1):
        limit = st["quotas"].get(depth, 0)
        used = st["used"].get(depth, 0)
        layers[depth] = {
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "categories": dict(st["cat_used"].get(depth, {})),
        }
    depth_counts = {d: 0 for d in range(1, st["max_depth"] + 1)}
    for record in st["seen"].values():
        depth_counts[record["depth"]] = depth_counts.get(record["depth"], 0) + 1

    return {
        "site": site,
        "limit": None,  # 站点总上限默认不设，逐层上限见 layers
        "used": len(st["seen"]),
        "layers": layers,
        "depth_counts": depth_counts,
        "queue_depth": len(st["seen"]),
        "enqueue_rate": st["rate"],
        "targets": [dict(record) for record in st["seen"].values()],
    }
