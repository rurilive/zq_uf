"""分页抓取，顺着 next 走，支持跨页判环与断点续抓。"""

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 排序、展示、会话类参数不影响页面内容，不参与判环。
IGNORED_PARAMS = frozenset({
    # 排序
    "sort", "sort_by", "sortby", "order", "order_by", "orderby", "dir", "direction",
    # 展示
    "display", "view", "layout", "theme",
    # 会话与追踪
    "session", "session_id", "sessionid", "sid", "token", "nonce", "timestamp", "ts", "_",
})
IGNORED_PREFIXES = ("utm_",)


def normalize_key(url):
    """归一化分页 URL：路径 + 关键参数（排序），忽略排序/展示/会话参数。"""
    parts = urlsplit(url)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in IGNORED_PARAMS
        and not k.lower().startswith(IGNORED_PREFIXES)
    ]
    kept.sort()
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path,
        urlencode(kept),
        "",
    ))


class WalkResult(list):
    """抓取到的页面 url 列表，附带停止原因与已访问集合。"""

    def __init__(self, pages, stop_reason, visited, repeated=None):
        super().__init__(pages)
        self.stop_reason = stop_reason  # done / limit / cycle / repeat
        self.visited = frozenset(visited)
        self.repeated = repeated  # 触发停止的重复 url，无则 None


def _save_state(state_path, start, pages, visited, next_url, stop_reason=None):
    state = {
        "start": start,
        "pages": list(pages),
        "visited": sorted(visited),
        "next": next_url,
        "stop_reason": stop_reason,
    }
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, state_path)


def _load_state(state_path):
    with open(state_path, encoding="utf-8") as fh:
        return json.load(fh)


def walk(fetch_page, start="http://shop/list?page=1", limit=100, *,
         stop_on_cycle=False, state_path=None, resume=False):
    """顺着 next 抓取分页，返回 WalkResult（list 子类）。

    stop_on_cycle=True 时按归一化键判环：重复访问立即停止，
    与上一页同键记为 repeat（连续重复），跨页重复记为 cycle。
    state_path 指定后每抓一页把分页状态和已访问集合落盘，
    resume=True 时从断点恢复并沿用已访问集合，换进程也能继续判环。
    """
    pages = []
    visited = set()
    url = start
    if resume and state_path and os.path.exists(state_path):
        state = _load_state(state_path)
        pages = list(state["pages"])
        visited = set(state["visited"])
        url = state["next"]

    stop_reason = None
    repeated = None
    last_key = None
    while url and len(pages) < limit:
        key = normalize_key(url)
        if stop_on_cycle and key in visited:
            stop_reason = "repeat" if key == last_key else "cycle"
            repeated = url
            break
        page = fetch_page(url)
        visited.add(key)
        last_key = key
        pages.append(page["url"])
        url = page.get("next")
        if state_path:
            _save_state(state_path, start, pages, visited, url)
    if stop_reason is None:
        stop_reason = "done" if url is None else "limit"
    if state_path:
        _save_state(state_path, start, pages, visited, url, stop_reason)
    return WalkResult(pages, stop_reason, visited, repeated)
