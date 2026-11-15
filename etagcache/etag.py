"""采集页面的条件请求缓存，仅使用 Python 标准库。

每个 URL 缓存以下内容：

* ``body``           页面正文（304 时不覆盖）
* ``etag``           服务器返回的 ETag，用于 ``If-None-Match``
* ``last_modified``  服务器返回的 Last-Modified，用于 ``If-Modified-Since``
* ``fingerprint``    正文的 sha256 指纹，用来判断内容是否真的变化
* ``fetched_at``     最近一次抓取（含 304 验证）成功的时间
* ``site``           所属站点，用于按站点限制容量

``headers(url)`` 按缓存里的验证器返回条件请求头；``store(url, response)``
记录响应，304 只刷新抓取时间，200 才写正文。容量按站点和总条数限制，
超出后按最近最少使用（LRU）淘汰。
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, OrderedDict
from typing import Any
from urllib.parse import urlsplit

_DEFAULT_MAX_ENTRIES = 500
_DEFAULT_MAX_ENTRIES_PER_SITE = 100

# URL -> 缓存条目；OrderedDict 从“最久未用”到“最近使用”排列
_store: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

_settings = {
    "max_entries": _DEFAULT_MAX_ENTRIES,
    "max_entries_per_site": _DEFAULT_MAX_ENTRIES_PER_SITE,
}

_stats = {
    "hits": 0,           # 304 命中且保留了缓存内容的次数
    "not_modified": 0,   # 观察到的 304 响应数（含无缓存条目的 304）
    "updates": 0,        # 写入/更新缓存内容的次数
    "evictions": 0,      # 累计淘汰条目数
}


def configure(max_entries: int | None = None,
              max_entries_per_site: int | None = None) -> dict[str, Any]:
    """调整容量上限；调小后立即按 LRU 淘汰多余条目。

    :param max_entries: 全部站点的缓存条目总数上限
    :param max_entries_per_site: 单个站点（netloc）的缓存条目数上限
    :return: 当前配置及本次因调小上限而淘汰的条目数
    """
    if max_entries is not None:
        _settings["max_entries"] = _check_limit(max_entries, "max_entries")
    if max_entries_per_site is not None:
        _settings["max_entries_per_site"] = _check_limit(
            max_entries_per_site, "max_entries_per_site")

    evicted = _enforce_capacity()
    return {
        "max_entries": _settings["max_entries"],
        "max_entries_per_site": _settings["max_entries_per_site"],
        "evicted": evicted,
    }


def clear() -> None:
    """清空缓存和统计计数，保留容量配置。"""
    _store.clear()
    for key in _stats:
        _stats[key] = 0


def stats() -> dict[str, Any]:
    """返回累计命中、304、更新、淘汰数以及当前条目和站点分布。"""
    per_site = Counter(entry["site"] for entry in _store.values())
    return {
        "hits": _stats["hits"],
        "not_modified": _stats["not_modified"],
        "updates": _stats["updates"],
        "evictions": _stats["evictions"],
        "entries": len(_store),
        "sites": dict(per_site),
    }


def fingerprint(body: Any) -> str:
    """计算正文的内容指纹（sha256），bytes 和 str 都支持。"""
    if body is None:
        data = b""
    elif isinstance(body, bytes):
        data = body
    else:
        data = str(body).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def headers(url: str) -> dict[str, str]:
    """返回该 URL 的条件请求头，并把它标记为最近使用。

    没有缓存条目、或条目没有验证器时返回空字典。
    """
    entry = _store.get(url)
    if entry is None:
        return {}

    # 本次请求会用到这条缓存，刷新其 LRU 位置
    _touch(url)

    conditional: dict[str, str] = {}
    if entry["etag"]:
        conditional["If-None-Match"] = entry["etag"]
    if entry["last_modified"]:
        conditional["If-Modified-Since"] = entry["last_modified"]
    return conditional


def store(url: str, response: dict[str, Any]) -> dict[str, Any]:
    """记录一次采集响应，并按容量限制淘汰最久未用的条目。

    * 200：写入正文、ETag、Last-Modified 和内容指纹。
    * 304：只刷新抓取时间，正文和指纹保持不变；响应带验证器时一并更新。
    * 其他状态码：不动缓存，避免错误页覆盖已有内容。

    返回字段：

    * ``hit``           304 且复用了缓存内容
    * ``not_modified``  是否 304
    * ``updated``       本次是否写入/改变了正文内容
    * ``evicted``       本次淘汰的条目数
    * ``stored``        调用后该 URL 是否仍在缓存中
    * ``cached``        兼容字段：``store`` 总是拿到真实响应，恒为 False
    * ``fetched_at``    缓存条目最近一次抓取时间
    * ``body``          调用后缓存中的正文
    """
    status = response.get("status", 200)
    response_headers = _header_map(response)

    etag = _clean(response_headers.get("etag") or response.get("etag"))
    last_modified = _clean(
        response_headers.get("last-modified") or response.get("last_modified"))

    hit = False
    updated = False
    evicted = 0
    not_modified = status == 304

    if status == 304:
        _stats["not_modified"] += 1
        entry = _store.get(url)
        if entry is not None:
            # 304 表示内容没变：只刷新抓取时间，正文/指纹保持原样
            if etag:
                entry["etag"] = etag
            if last_modified:
                entry["last_modified"] = last_modified
            entry["fetched_at"] = time.time()
            _touch(url)
            hit = True
            _stats["hits"] += 1

    elif status == 200:
        body = response.get("body")
        if body is None:
            body = ""
        digest = fingerprint(body)
        now = time.time()
        entry = _store.get(url)

        if entry is None:
            _store[url] = {
                "body": body,
                "etag": etag,
                "last_modified": last_modified,
                "fingerprint": digest,
                "fetched_at": now,
                "site": _site(url),
            }
            updated = True
            _stats["updates"] += 1
        else:
            updated = entry["fingerprint"] != digest
            entry.update(
                body=body,
                etag=etag,
                last_modified=last_modified,
                fingerprint=digest,
                fetched_at=now,
            )
            if updated:
                _stats["updates"] += 1
            _touch(url)

        evicted = _enforce_capacity(protected=url)

    entry = _store.get(url)
    return {
        "url": url,
        "stored": entry is not None,
        "status": status,
        "cached": False,
        "hit": hit,
        "not_modified": not_modified,
        "updated": updated,
        "evicted": evicted,
        "fetched_at": entry["fetched_at"] if entry is not None else None,
        "body": entry["body"] if entry is not None else None,
    }


def _check_limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} 必须是 >= 1 的整数")
    return value


def _site(url: str) -> str:
    """站点标识：优先用 netloc（含端口），没有则退回整个 URL。"""
    netloc = urlsplit(url).netloc.lower()
    return netloc or url


def _touch(url: str) -> None:
    """把条目移动到 LRU 队列尾部（最近使用）。"""
    if url in _store:
        _store.move_to_end(url)


def _header_map(response: dict[str, Any]) -> dict[str, str]:
    """把响应头统一成小写键，兼容 dict/list 形式。"""
    raw = response.get("headers") or {}
    items = raw.items() if hasattr(raw, "items") else raw
    headers: dict[str, str] = {}
    for key, value in items:
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        headers[str(key).lower()] = "" if value is None else str(value)
    return headers


def _clean(value: Any) -> str | None:
    """验证器统一成去掉首尾空白的字符串，空值视为没有。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _enforce_capacity(protected: str | None = None) -> int:
    """先按站点、再按总数淘汰最久未用的条目。

    ``protected`` 是刚写入的 URL，正常情况下它位于队尾，不会被选中；
    这里显式跳过它，避免极端配置下把刚存的内容立刻淘汰。
    """
    evicted = 0

    # 1) 每个站点各自的条数上限
    while True:
        counts = Counter(entry["site"] for entry in _store.values())
        over = {site for site, count in counts.items()
                if count > _settings["max_entries_per_site"]}
        if not over:
            break
        victim = next(
            (url for url, entry in _store.items()
             if entry["site"] in over and url != protected),
            None,
        )
        if victim is None:
            break
        del _store[victim]
        evicted += 1

    # 2) 所有站点的总条数上限
    while len(_store) > _settings["max_entries"]:
        victim = next((url for url in _store if url != protected), None)
        if victim is None:
            break
        del _store[victim]
        evicted += 1

    _stats["evictions"] += evicted
    return evicted
