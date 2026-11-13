"""可恢复的游标分页抓取实现（仅依赖 Python 标准库）。"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CURSOR_FIELD = "next_cursor"
CHECKPOINT_VERSION = 1
DEFAULT_REQUIRED_FIELDS = ("id", "url")
DEFAULT_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "spm",
        "scm",
        "ref",
        "referrer",
        "from",
        "track_id",
        "tracking_id",
        "shareid",
        "share_token",
    }
)


@dataclass
class Item:
    item_id: str
    title: str
    price: str
    url: str


def strip_tracking_params(url, tracking_params=DEFAULT_TRACKING_PARAMS):
    """去掉 URL 上的追踪参数，追踪参数名按小写匹配。"""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in tracking_params
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def item_fingerprint(item_id, url, tracking_params=DEFAULT_TRACKING_PARAMS):
    """URL 去追踪参数后算指纹；URL 为空时退回商品 ID。"""
    basis = strip_tracking_params(url, tracking_params) if url else str(item_id)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def parse_page(payload, on_invalid=None, required_fields=DEFAULT_REQUIRED_FIELDS):
    """解析单页商品；字段缺失的记录回调样本后跳过，不影响同页其他记录。"""
    items = []
    for raw in (payload or {}).get("items") or []:
        if not isinstance(raw, dict):
            if on_invalid is not None:
                on_invalid({"raw": raw, "reason": "record_not_object"})
            continue
        item_id = str(raw.get("id") or "")
        url = str(raw.get("url") or "")
        normalized = {"id": item_id, "url": url}
        missing = [field for field in required_fields if not normalized.get(field)]
        if missing:
            if on_invalid is not None:
                on_invalid(
                    {
                        "raw": raw,
                        "reason": "missing_required_field",
                        "missing_fields": missing,
                    }
                )
            continue
        items.append(
            Item(
                item_id=item_id,
                title=raw.get("title", ""),
                price=raw.get("price", ""),
                url=url,
            )
        )
    return items


def _is_rate_limited(exc):
    code = (
        getattr(exc, "code", None)
        or getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
    )
    return code == 429


def _retry_after_seconds(exc):
    headers = getattr(exc, "headers", None)
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _request_with_backoff(
    fetch_page,
    cursor,
    *,
    max_retries,
    backoff_start,
    backoff_factor,
    backoff_max,
    sleep,
):
    attempt = 0
    delay = backoff_start
    while True:
        try:
            return fetch_page(cursor)
        except Exception as exc:
            if not _is_rate_limited(exc) or attempt >= max_retries:
                raise
            retry_after = _retry_after_seconds(exc)
            sleep(retry_after if retry_after is not None else min(delay, backoff_max))
            attempt += 1
            delay = min(delay * backoff_factor, backoff_max)


def _write_json_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _append_jsonl(path, record):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _advance_cursor(cursor):
    # 响应里没有游标字段时，退回页码 +1，兼容老的按页码接口。
    if isinstance(cursor, bool):
        return cursor
    if isinstance(cursor, int):
        return cursor + 1
    if isinstance(cursor, str) and cursor.isdigit():
        return str(int(cursor) + 1)
    return cursor


def fetch_all(
    fetch_page,
    *,
    initial_cursor=1,
    cursor_field=CURSOR_FIELD,
    checkpoint_path=None,
    invalid_sample_path=None,
    rate_interval=0.0,
    backoff_start=1.0,
    backoff_factor=2.0,
    backoff_max=60.0,
    max_retries=5,
    tracking_params=DEFAULT_TRACKING_PARAMS,
    sleep=time.sleep,
):
    """游标翻页抓取全部商品，返回 Item 列表（结构与旧版一致）。

    每页成功后把下一页游标和已抓商品写入 checkpoint_path（JSON），
    重启时自动从断点继续；空页视为正常结束。
    """
    cursor = initial_cursor
    items = []
    fingerprint_index = {}
    requests = 0

    if checkpoint_path and os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        if state.get("done"):
            return [Item(**raw) for raw in state.get("items", [])]
        if "cursor" in state:
            cursor = state["cursor"]
        for raw in state.get("items", []):
            item = Item(**raw)
            fingerprint = item_fingerprint(item.item_id, item.url, tracking_params)
            fingerprint_index[fingerprint] = len(items)
            items.append(item)

    def record_invalid(sample):
        record = {
            "cursor": cursor,
            "ts": datetime.now(timezone.utc).isoformat(),
            **sample,
        }
        if invalid_sample_path:
            _append_jsonl(invalid_sample_path, record)

    def persist(done):
        if not checkpoint_path:
            return
        _write_json_atomic(
            checkpoint_path,
            {
                "version": CHECKPOINT_VERSION,
                "cursor": cursor,
                "done": done,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "requests": requests,
                "upserted": len(items),
                "items": [dataclasses.asdict(item) for item in items],
            },
        )

    first_request = True
    while True:
        if not first_request and rate_interval > 0:
            sleep(rate_interval)
        payload = _request_with_backoff(
            fetch_page,
            cursor,
            max_retries=max_retries,
            backoff_start=backoff_start,
            backoff_factor=backoff_factor,
            backoff_max=backoff_max,
            sleep=sleep,
        )
        first_request = False
        requests += 1
        if not isinstance(payload, dict):
            payload = {}

        next_cursor = payload.get(cursor_field)
        page_items = parse_page(payload, on_invalid=record_invalid)
        for item in page_items:
            fingerprint = item_fingerprint(item.item_id, item.url, tracking_params)
            if fingerprint in fingerprint_index:
                items[fingerprint_index[fingerprint]] = item  # 覆盖更新
            else:
                fingerprint_index[fingerprint] = len(items)
                items.append(item)

        if next_cursor not in (None, ""):
            cursor = next_cursor
            persist(done=False)
            continue
        if not page_items:
            break  # 空页代表抓取结束，不是失败
        # 响应不再给游标：数字游标按老接口页码 +1；不透明游标直接结束，
        # 避免停在同一页重复请求。
        advanced = _advance_cursor(cursor)
        if advanced == cursor:
            break
        cursor = advanced
        persist(done=False)

    persist(done=True)
    return items
