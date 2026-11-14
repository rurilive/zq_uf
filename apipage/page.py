"""接口分页：游标翻页加断点恢复。

fetch 约定：
- 入参是游标 token，首页固定调用 ``fetch(1)``；返回
  ``{"items": [...], "next_cursor": "下一页游标"}``
- ``next_cursor`` 显式为 None 表示没有下一页；旧的 ``next_page`` 字段继续兼容
- 游标失效时 fetch 抛出 :class:`CursorExpired`，walk 会回首页重取游标，
  并依据 seen_keys 跳过已经抓过的页
- 相邻两页返回同一个 next_cursor 视为重复，walk 立即停止，避免死循环
"""

import json

STATE_VERSION = 2
HOME_TOKEN = 1


class CursorExpired(Exception):
    """fetch 抛出：当前游标已失效，需要回首页重新取游标。"""


def _item_key(item):
    """取条目的去重键：dict 用 id，其余用条目本身。"""
    if isinstance(item, dict):
        if "id" in item:
            return item["id"]
        return json.dumps(item, sort_keys=True, ensure_ascii=False)
    return item


def _normalize(state):
    """迁移并校验断点状态，始终返回当前版本的完整状态。

    没有 version 字段的旧格式（``{"page": n}``）按 v1 自动迁移，
    无法识别的新版本直接报错而不是猜测着读。
    """
    if state is None:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("断点状态必须是 dict")

    version = state.get("version", 1)
    if version == 1:
        page = state.get("page", 1)
        state = {
            "version": STATE_VERSION,
            "cursor": None,
            "page": page,
            "seen_keys": [],
            "pages_fetched": max(page - 1, 0),
        }
    elif version == STATE_VERSION:
        state = dict(state)
        state.setdefault("cursor", None)
        state.setdefault("page", 1)
        state.setdefault("seen_keys", [])
        state.setdefault("pages_fetched", max(state["page"] - 1, 0))
    else:
        raise ValueError("不支持的断点版本: %r" % version)

    page = state["page"]
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page 必须是 >= 1 的整数")
    if state["cursor"] is not None and not isinstance(state["cursor"], str):
        raise ValueError("cursor 必须是字符串或 None")
    if not isinstance(state["seen_keys"], list):
        raise ValueError("seen_keys 必须是列表")
    return state


def walk(fetch, state=None):
    """顺着游标翻页，返回本次拉取结果和可落盘的断点状态。

    返回字段：
    - items: 本次新抓到的条目（已抓键对应的条目不会重复出现）
    - page: 下一次请求的页码（兼容旧字段）
    - pages: 本次产出新条目的页数，不含跳过页
    - duplicate / duplicate_cursor: 是否因游标连续重复而停止
    - skipped_pages: 全部条目都已抓过、被跳过的页数
    - expired: 游标失效后回首页恢复的次数
    - resumed: 是否从断点状态启动
    - state: 版本化断点，可直接交给 save_checkpoint 或下一次 walk
    """
    state = _normalize(state)
    seen = {key: None for key in state["seen_keys"]}
    page = state["page"]
    token = state["cursor"] or page
    pages_fetched = state["pages_fetched"]
    resumed = state["cursor"] is not None or bool(seen) or page > 1

    rows = []
    pages = 0
    skipped_pages = 0
    expired = 0
    duplicate = False
    duplicate_cursor = None
    last_cursor = None
    recovered = set()

    while True:
        try:
            payload = fetch(token)
        except CursorExpired:
            # 首页仍失效、或同一游标已恢复过一次：恢复无法收敛，抛出由调用方处理
            if token == HOME_TOKEN or token in recovered:
                raise
            recovered.add(token)
            expired += 1
            token = HOME_TOKEN
            page = 1
            last_cursor = None
            continue

        if not isinstance(payload, dict):
            raise ValueError("fetch 返回值必须是 dict")
        items = payload.get("items") or []
        if not items:
            break

        unseen = [item for item in items if _item_key(item) not in seen]
        if unseen:
            rows.extend(unseen)
            seen.update((_item_key(item), None) for item in unseen)
            pages += 1
            pages_fetched += 1
        else:
            # 整页都抓过：跳过内容，但仍顺着本页游标继续往后翻
            skipped_pages += 1

        if "next_cursor" in payload:
            nxt = payload["next_cursor"]
            if nxt is None:
                page += 1
                break
            if nxt == last_cursor:
                duplicate = True
                duplicate_cursor = nxt
                page += 1
                break
            last_cursor = nxt
            token = nxt
            page += 1
        elif "next_page" in payload:
            token = payload["next_page"]
            page = token if isinstance(token, int) and not isinstance(token, bool) else page + 1
        else:
            page += 1
            token = page

    checkpoint = {
        "version": STATE_VERSION,
        "cursor": token if isinstance(token, str) else None,
        "page": page,
        "seen_keys": list(seen.keys()),
        "pages_fetched": pages_fetched,
    }
    return {
        "items": rows,
        "page": page,
        "pages": pages,
        "duplicate": duplicate,
        "duplicate_cursor": duplicate_cursor,
        "skipped_pages": skipped_pages,
        "expired": expired,
        "resumed": resumed,
        "state": checkpoint,
    }


def resume(state):
    """校验断点并返回可传给 walk 的状态；旧格式在此完成迁移。"""
    return _normalize(state)


def save_checkpoint(path, state):
    """把断点状态按当前版本写入 JSON 文件。"""
    data = _normalize(state)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_checkpoint(path):
    """读取断点文件；无版本号的旧格式自动迁移为当前版本。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _normalize(data)
