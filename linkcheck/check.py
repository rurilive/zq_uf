"""并发去重死链巡检引擎。

口径：
- check(urls, workers) 接口不变；同一 URL（规范化后）全程只请求一次，
  结果按 URL 缓存复用，重复引用只累加 references。
- ThreadPoolExecutor 并发；同一站点（host:port）串行化并强制最小间隔，
  即“跨站点并发、同站点限速”。
- 3xx 不自动跟随，由引擎逐跳记录完整重定向链；检测环和跳转上限。
- 失败按状态/异常分类，超时与连接错误（含 5xx）按指数退避重试。
- 结果保留在模块级缓存中，export_jsonl 只导出增量部分。
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from .transport import (
    CAT_INVALID,
    RETRYABLE_CATEGORIES,
    UrllibTransport,
)

# 结果状态
OK = "ok"
DEAD = "dead"          # 4xx / 5xx 终态
REDIRECT = "redirect"  # 正常跟随到 2xx 的重定向链
LOOP = "loop"          # 重定向环
LIMIT = "redirect_limit"  # 超过最大跳数
ERROR = "error"        # 传输层/参数类错误

DEFAULT_MAX_REDIRECTS = 10
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.1          # 指数退避基数（秒）：backoff * 2**attempt
DEFAULT_SITE_INTERVAL = 0.2    # 同站点两次请求的最小间隔（秒）
HEAD_FALLBACK_CODES = frozenset({403, 405, 501})  # HEAD 不支持时回退 GET

# 兼容旧测试的模块级结果列表（每次 check 覆盖为本轮去重后的结果）。
_results = []

# 模块级 URL -> 结果行缓存，跨 check 调用复用。
_cache = {}
_cache_lock = threading.Lock()

# 已增量导出过的结果行 id 集合。
_exported_ids = set()

# 默认传输，可用 set_transport 替换（测试用）。
_transport = None
_transport_lock = threading.Lock()


def get_transport():
    global _transport
    if _transport is None:
        with _transport_lock:
            if _transport is None:
                _transport = UrllibTransport()
    return _transport


def set_transport(transport):
    """注入自定义传输（需实现 request(url, method)）。"""
    global _transport
    with _transport_lock:
        _transport = transport


def reset():
    """清空结果、缓存和导出记录（测试隔离用）。"""
    with _cache_lock:
        _results.clear()
        _cache.clear()
        _exported_ids.clear()


def _site_of(url):
    """提取站点 key（host:port），非法 URL 归入 invalid。"""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            return "invalid"
        return parts.netloc
    except (ValueError, TypeError):
        return "invalid"


class _SiteGate:
    """每个站点一把锁 + 最小请求间隔，实现按站点限速。"""

    def __init__(self, min_interval):
        self.min_interval = min_interval
        self._locks = {}
        self._next_slot = {}

    @contextlib.contextmanager
    def acquire(self, site):
        lock = self._locks.setdefault(site, threading.Lock())
        with lock:
            wait = self._next_slot.get(site, 0.0) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                self._next_slot[site] = time.monotonic() + self.min_interval


class LinkChecker:
    def __init__(
        self,
        transport=None,
        max_redirects=DEFAULT_MAX_REDIRECTS,
        retries=DEFAULT_RETRIES,
        backoff=DEFAULT_BACKOFF,
        site_interval=DEFAULT_SITE_INTERVAL,
    ):
        self.transport = transport
        self.max_redirects = max_redirects
        self.retries = retries
        self.backoff = backoff
        self.gate = _SiteGate(site_interval)

    def _request_with_retry(self, url, method, site):
        """单跳请求 + 分类重试，返回 (FetchResponse 或 None, 类别, 尝试次数)。"""
        transport = self.transport or get_transport()
        attempt = 0
        while True:
            try:
                with self.gate.acquire(site):
                    resp = transport.request(url, method)
            except Exception as exc:  # 传输层异常必然带 category
                category = getattr(exc, "category", "connection")
                if category in RETRYABLE_CATEGORIES and attempt < self.retries:
                    time.sleep(self.backoff * 2 ** attempt)
                    attempt += 1
                    continue
                return None, category, attempt + 1
            if resp.status >= 500 and attempt < self.retries:
                # 5xx 服务端临时故障，退避后重试。
                time.sleep(self.backoff * 2 ** attempt)
                attempt += 1
                continue
            return resp, f"http_{resp.status}", attempt + 1

    def check_url(self, url):
        """检查单个去重后的 URL，产出完整结果行。"""
        started = time.perf_counter()
        site = _site_of(url)
        chain = []          # 重定向链：每跳 {"url", "status", "location"}
        seen = {}           # URL -> 在链中的位置，用于环检测
        current = url
        attempts_total = 0

        while True:
            resp, category, attempts = self._request_with_retry(current, "HEAD", site)
            attempts_total += attempts
            if resp is None:
                return self._row(
                    url, site, ERROR, None, chain, category,
                    attempts_total, started, loop_index=None,
                )
            if resp.status in HEAD_FALLBACK_CODES:
                # 站点不接受 HEAD，用 GET 再试一次（不计重定向跳数）。
                resp, category, attempts = self._request_with_retry(
                    current, "GET", site
                )
                attempts_total += attempts
                if resp is None:
                    return self._row(
                        url, site, ERROR, None, chain, category,
                        attempts_total, started, loop_index=None,
                    )

            location = resp.location()
            chain.append({
                "url": current,
                "status": resp.status,
                "location": location,
            })

            if 300 <= resp.status < 400 and location:
                next_url = urljoin(current, location)
                if next_url in seen:
                    # 环：记录回到链中第几跳。
                    return self._row(
                        url, site, LOOP, None, chain, None,
                        attempts_total, started, loop_index=seen[next_url],
                    )
                seen[current] = len(chain) - 1
                if len(chain) >= self.max_redirects:
                    return self._row(
                        url, site, LIMIT, None, chain, "too_many_redirects",
                        attempts_total, started, loop_index=None,
                    )
                current = next_url
                continue

            # 终态：2xx 成功（有链则为重定向），其余为死链。
            if resp.status < 400:
                status = REDIRECT if len(chain) > 1 else OK
                category = None
            else:
                status = DEAD
            return self._row(
                url, site, status, current, chain, category,
                attempts_total, started, loop_index=None,
            )

    def _row(self, url, site, status, final_url, chain,
             category, attempts, started, loop_index):
        row = {
            "url": url,
            "site": site,
            "status": status,
            "http_status": (
                chain[-1]["status"] if chain and status != ERROR else None
            ),
            "category": category,
            "final_url": final_url,
            "chain": chain,
            "hops": max(len(chain) - 1, 0),
            "loop_index": loop_index,
            "attempts": attempts,
            "references": 1,
            "elapsed": round(time.perf_counter() - started, 6),
        }
        row["id"] = id(row)  # 增量导出去重标识
        return row


def check(
    urls,
    workers=1,
    *,
    transport=None,
    max_redirects=DEFAULT_MAX_REDIRECTS,
    retries=DEFAULT_RETRIES,
    backoff=DEFAULT_BACKOFF,
    site_interval=DEFAULT_SITE_INTERVAL,
    export_path=None,
    reuse=True,
):
    """并发去重巡检。

    urls: 待查链接，可含重复引用；workers: 并发线程数。
    返回汇总字典；逐 URL 明细通过 report() 获取。
    """
    started = time.monotonic()
    urls = list(urls)
    checker = LinkChecker(
        transport=transport,
        max_redirects=max_redirects,
        retries=retries,
        backoff=backoff,
        site_interval=site_interval,
    )

    # 保持首次出现顺序地去重，同时统计每个 URL 被引用次数。
    unique, order, refcount = [], {}, {}
    for url in urls:
        if url not in refcount:
            refcount[url] = 0
            order[url] = len(unique)
            unique.append(url)
        refcount[url] += 1

    rows = [None] * len(unique)
    fresh, cached = [], 0
    with _cache_lock:
        for index, url in enumerate(unique):
            row = _cache.get(url)
            if row is not None and reuse:
                row["references"] += refcount[url]
                rows[index] = row
                cached += 1
            else:
                fresh.append((index, url))

    actual_workers = max(1, min(workers, len(fresh)))
    requests_issued = 0
    if fresh:
        with ThreadPoolExecutor(max_workers=actual_workers) as pool:
            futures = {
                pool.submit(checker.check_url, url): (index, url)
                for index, url in fresh
            }
            for future in futures:
                index, url = futures[future]
                row = future.result()
                row["references"] = refcount[url]
                rows[index] = row
                with _cache_lock:
                    _cache[url] = row
                requests_issued += row["attempts"]

    # 本轮结果覆盖模块级列表（顺序与输入首次出现一致）。
    with _cache_lock:
        _results.clear()
        _results.extend(rows)

    exported = None
    if export_path is not None:
        exported = export_jsonl(export_path)

    summary = summarize(rows)
    summary.update({
        "checked": len(urls),
        "unique": len(unique),
        "workers": actual_workers,
        "cached": cached,
        "requests_issued": requests_issued,
        "elapsed": round(time.monotonic() - started, 6),
    })
    if exported is not None:
        summary["exported"] = exported
    return summary


def report():
    """返回本轮巡检的逐 URL 明细（浅拷贝，避免外部改坏内部列表）。"""
    with _cache_lock:
        return list(_results)


def summarize(rows):
    """按状态和失败分类汇总。"""
    by_status, by_category = {}, {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        if row["category"]:
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    dead = sum(by_status.get(s, 0) for s in (DEAD, ERROR, LOOP, LIMIT))
    return {
        "total": len(rows),
        "dead": dead,
        "by_status": by_status,
        "by_category": by_category,
    }


def site_report():
    """巡检报告按站点汇总：每站链接数、死链/重定向/环、耗时。"""
    with _cache_lock:
        rows = list(_results)
    sites = {}
    for row in rows:
        item = sites.setdefault(row["site"], {
            "site": row["site"], "urls": 0, "dead": 0,
            "redirects": 0, "loops": 0, "elapsed": 0.0,
        })
        item["urls"] += 1
        item["elapsed"] += row["elapsed"]
        if row["status"] in (DEAD, ERROR, LIMIT):
            item["dead"] += 1
        elif row["status"] == REDIRECT:
            item["redirects"] += 1
        elif row["status"] == LOOP:
            item["loops"] += 1
    for item in sites.values():
        item["elapsed"] = round(item["elapsed"], 6)
    return sorted(sites.values(), key=lambda item: item["site"])


def export_jsonl(path):
    """增量导出：只追加此前没导出过的结果行，返回本次写入条数。"""
    with _cache_lock:
        pending = [row for row in _results if row["id"] not in _exported_ids]
        with open(path, "a", encoding="utf-8") as handle:
            for row in pending:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                _exported_ids.add(row["id"])
        return len(pending)
