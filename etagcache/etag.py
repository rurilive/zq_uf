"""采集条件请求缓存。

- 请求侧带回站点给过的 ETag / Last-Modified；
- 站点返回 304 时只刷新抓取时间，绝不用空 body 覆盖已有内容；
- 缓存按站点条数和总条数做 LRU 淘汰，淘汰后下一轮自然是全量下载；
- 命中情况按站点、按天统计，容量淘汰和人工清理都留记录，可查询、可导出。
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlsplit

DEFAULT_MAX_PER_SITE = 100
DEFAULT_MAX_TOTAL = 1000

_max_per_site = DEFAULT_MAX_PER_SITE
_max_total = DEFAULT_MAX_TOTAL

_store: dict[str, dict] = {}
_events: list[dict] = []
_evictions: list[dict] = []
_clock = 0


def _now() -> datetime:
    return datetime.now().astimezone()


def _site(url: str) -> str:
    return urlsplit(url).netloc or url.split("/", 1)[0]


def _touch(entry: dict) -> None:
    global _clock
    _clock += 1
    entry["last_used"] = _clock


def _remove(url: str, reason: str) -> dict:
    entry = _store.pop(url)
    now = _now()
    record = {
        "site": entry["site"],
        "url": url,
        "reason": reason,
        "at": now.isoformat(),
        "day": now.date().isoformat(),
    }
    _evictions.append(record)
    return record


def _evict() -> int:
    """按站点条数和总条数做 LRU 淘汰，返回本次淘汰数量。"""
    evicted = 0
    while True:
        changed = False
        by_site: dict[str, list[str]] = defaultdict(list)
        for url, entry in _store.items():
            by_site[entry["site"]].append(url)
        for urls in by_site.values():
            while len(urls) > _max_per_site:
                victim = min(urls, key=lambda u: (_store[u]["last_used"], u))
                urls.remove(victim)
                _remove(victim, "capacity")
                evicted += 1
                changed = True
        if len(_store) > _max_total:
            victim = min(_store, key=lambda u: (_store[u]["last_used"], u))
            _remove(victim, "capacity")
            evicted += 1
            changed = True
        if not changed:
            return evicted


def _wants_conditional_headers(entry: dict) -> dict:
    out = {}
    if entry.get("etag"):
        out["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        out["If-Modified-Since"] = entry["last_modified"]
    return out


def _validator(response: dict, name: str):
    key = name.lower().replace("-", "_")
    for k, v in response.items():
        if isinstance(v, str) and k.lower().replace("-", "_") == key and v:
            return v
    headers = response.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == name.lower() and v:
            return v
    return None


def configure(max_per_site: int | None = None, max_total: int | None = None) -> dict:
    """调整缓存上限；返回当前配置。"""
    global _max_per_site, _max_total
    if max_per_site is not None:
        _max_per_site = max(0, int(max_per_site))
    if max_total is not None:
        _max_total = max(0, int(max_total))
    return {"max_per_site": _max_per_site, "max_total": _max_total}


def headers(url: str) -> dict:
    """有缓存时返回条件请求头，否则返回空字典（全量下载）。"""
    entry = _store.get(url)
    if entry is None:
        return {}
    _touch(entry)
    return _wants_conditional_headers(entry)


def store(url: str, response: dict) -> dict:
    """处理一次抓取结果。

    200：保存/更新内容和版本标识；
    304：只更新抓取时间，保留原内容和版本标识；
    其他状态：不动缓存。
    """
    try:
        status = int(response.get("status", 200))
    except (TypeError, ValueError):
        status = 200

    site = _site(url)
    now = _now()
    stamp = now.isoformat()
    day = now.date().isoformat()

    entry = _store.get(url)
    # hit：本轮请求带上了条件头、复用了已有缓存版本；
    # not_modified：站点明确回复 304；两者分开统计。
    hit = entry is not None and bool(_wants_conditional_headers(entry))
    not_modified = content_updated = stored = False
    evicted = 0

    if status == 304:
        not_modified = True
        if entry is not None:
            stored = True
            entry["fetched_at"] = stamp
            _touch(entry)
    elif 200 <= status < 300:
        body = response.get("body", "")
        if body is None:
            body = ""
        etag = _validator(response, "ETag")
        last_modified = _validator(response, "Last-Modified")
        if entry is None:
            entry = {
                "url": url,
                "site": site,
                "body": body,
                "etag": etag,
                "last_modified": last_modified,
                "fetched_at": stamp,
                "last_used": 0,
            }
            _store[url] = entry
            content_updated = True
        else:
            content_updated = entry["body"] != body
            entry["body"] = body
            entry["etag"] = etag
            entry["last_modified"] = last_modified
            entry["fetched_at"] = stamp
        _touch(entry)
        stored = True
        evicted = _evict()

    _events.append({
        "site": site,
        "url": url,
        "day": day,
        "at": stamp,
        "hit": hit,
        "not_modified": not_modified,
        "content_updated": content_updated,
        "evicted": evicted,
    })

    return {
        "url": url,
        "site": site,
        "stored": stored,
        "hit": hit,
        "cached": hit,
        "not_modified": not_modified,
        "content_updated": content_updated,
        "evicted": evicted,
    }


def cached(url: str) -> dict | None:
    """查看某条缓存（body、etag、last_modified、fetched_at），没有则为 None。"""
    entry = _store.get(url)
    return dict(entry) if entry is not None else None


def cache_size() -> int:
    return len(_store)


def _as_day(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def stats(site: str | None = None, day=None) -> list[dict]:
    """按站点、按天统计命中情况。day 可传 date 或 'YYYY-MM-DD'。"""
    wanted_day = _as_day(day)
    rows: dict[tuple[str, str], dict] = {}
    for event in _events:
        if site is not None and event["site"] != site:
            continue
        if wanted_day is not None and event["day"] != wanted_day:
            continue
        key = (event["site"], event["day"])
        row = rows.setdefault(key, {
            "site": event["site"],
            "day": event["day"],
            "requests": 0,
            "hits": 0,
            "misses": 0,
            "not_modified": 0,
            "content_updated": 0,
            "evicted": 0,
        })
        row["requests"] += 1
        row["hits"] += 1 if event["hit"] else 0
        row["misses"] += 0 if event["hit"] else 1
        row["not_modified"] += 1 if event["not_modified"] else 0
        row["content_updated"] += 1 if event["content_updated"] else 0
        row["evicted"] += event["evicted"]
    result = []
    for key in sorted(rows):
        row = rows[key]
        row["hit_rate"] = round(row["hits"] / row["requests"], 4) if row["requests"] else 0.0
        result.append(row)
    return result


def eviction_records(site: str | None = None, since=None, until=None,
                     reason: str | None = None) -> list[dict]:
    """查询清理记录；可按站点、日期范围和原因筛选，用于导出和回看。"""
    since_day = _as_day(since)
    until_day = _as_day(until)
    out = []
    for record in _evictions:
        if site is not None and record["site"] != site:
            continue
        if reason is not None and record["reason"] != reason:
            continue
        if since_day is not None and record["day"] < since_day:
            continue
        if until_day is not None and record["day"] > until_day:
            continue
        out.append(dict(record))
    return out


def eviction_stats(site: str | None = None) -> list[dict]:
    """按站点汇总清理数量（容量淘汰与人工清理分开计数）。"""
    rows: dict[str, dict] = {}
    for record in eviction_records(site=site):
        row = rows.setdefault(record["site"], {
            "site": record["site"],
            "cleared": 0,
            "capacity": 0,
            "manual": 0,
            "last_at": None,
        })
        row["cleared"] += 1
        row[record["reason"]] = row.get(record["reason"], 0) + 1
        row["last_at"] = record["at"]
    return [rows[key] for key in sorted(rows)]


def clear(url: str | None = None, site: str | None = None) -> dict:
    """人工清理缓存；返回按站点的清理数量。清理后下一轮自动全量下载。"""
    targets = [
        u for u, entry in _store.items()
        if (url is None or u == url) and (site is None or entry["site"] == site)
    ]
    by_site: dict[str, int] = defaultdict(int)
    for target in targets:
        by_site[_store[target]["site"]] += 1
        _remove(target, "manual")
    return {"cleared": len(targets), "by_site": dict(by_site)}


def _to_csv(columns: list[str], rows: list[dict], path) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    text = buffer.getvalue()
    if path is not None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return text


def export_stats(path=None, site: str | None = None, day=None) -> str:
    """导出命中统计 CSV；传 path 时同时写入文件，返回 CSV 文本。"""
    columns = ["site", "day", "requests", "hits", "misses", "hit_rate",
               "not_modified", "content_updated", "evicted"]
    return _to_csv(columns, stats(site=site, day=day), path)


def export_evictions(path=None, site: str | None = None, since=None, until=None,
                     reason: str | None = None) -> str:
    """导出清理记录 CSV；传 path 时同时写入文件，返回 CSV 文本。"""
    columns = ["site", "url", "reason", "day", "at"]
    records = eviction_records(site=site, since=since, until=until, reason=reason)
    return _to_csv(columns, records, path)


def reset(keep_history: bool = False) -> None:
    """清空内存状态，供测试使用；keep_history=True 时保留清理记录。"""
    global _clock
    _store.clear()
    _events.clear()
    _clock = 0
    if not keep_history:
        _evictions.clear()
