"""批量抓取详情页：连接复用、模板分组、错误分类、批次统计。"""
from __future__ import annotations

import logging
import threading
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .fetcher import FetchError, HttpFetcher, HttpStatusError, TimeoutFetchError
from .templates import ParseError, TemplateRegistry

logger = logging.getLogger("detailcrawl")

Record = namedtuple("Record", "url detail attempts elapsed")

_MAX_WORKERS = 32


class BatchStats:
    """一个批次的成功/跳过/失败明细与分阶段耗时。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.succeeded = []
        self.skipped = []
        self.failed = []
        self.fetch_seconds = 0.0
        self.parse_seconds = 0.0
        self.wall_seconds = 0.0
        self.templates = {}
        self.rules_built = 0

    def record(self, bucket, url, detail, attempts, elapsed):
        with self._lock:
            getattr(self, bucket).append(Record(url, detail, attempts, elapsed))

    def add_fetch_seconds(self, seconds):
        with self._lock:
            self.fetch_seconds += seconds

    def add_parse_seconds(self, seconds):
        with self._lock:
            self.parse_seconds += seconds

    @property
    def total(self):
        return len(self.succeeded) + len(self.skipped) + len(self.failed)

    def summary(self):
        return (
            f"total={self.total} ok={len(self.succeeded)} "
            f"skipped={len(self.skipped)} failed={len(self.failed)} "
            f"fetch={self.fetch_seconds:.3f}s parse={self.parse_seconds:.3f}s "
            f"wall={self.wall_seconds:.3f}s"
        )

    def details(self):
        lines = [self.summary()]
        for label, records in (
            ("ok", self.succeeded),
            ("skipped", self.skipped),
            ("failed", self.failed),
        ):
            for rec in records:
                lines.append(
                    f"[{label}] {rec.url} attempts={rec.attempts} "
                    f"elapsed={rec.elapsed:.3f}s {rec.detail}"
                )
        return "\n".join(lines)


class CrawlResult(list):
    """按输入顺序排列的结果列表，附带批次统计 stats。"""

    def __init__(self, items, stats):
        super().__init__(items)
        self.stats = stats


def _classify(exc):
    """返回 (记录桶, 是否可重试, 原因)。只有超时和 5xx 可重试。"""
    if isinstance(exc, HttpStatusError):
        bucket = "failed" if exc.status >= 500 else "skipped"
        return bucket, exc.retryable, exc.reason
    if isinstance(exc, (TimeoutFetchError, TimeoutError)):
        return "failed", True, "timed out"
    if isinstance(exc, FetchError):
        return "failed", False, exc.reason
    return "failed", False, f"{type(exc).__name__}: {exc}"


def crawl(urls, fetch=None, *, parse=None, max_per_host=4, retries=2,
          timeout=10.0, backoff=0.1):
    """批量抓取详情页，返回与输入顺序一致的 CrawlResult。

    fetch: 自定义抓取函数；缺省用内置 HttpFetcher（按域名复用连接）。
           自定义 fetch 返回 None 视为未找到，记入跳过。
    parse: 缺省时内置抓取按模板解析字段、自定义抓取原样返回；
           "template" 强制模板分组解析；可调用对象则逐页直接调用。
    max_per_host: 单域名并发上限。
    retries: 超时/5xx 的最大重试次数（不含首次尝试）。
    """
    urls = list(urls)
    stats = BatchStats()
    results = [None] * len(urls)
    started = time.perf_counter()
    if not urls:
        stats.wall_seconds = time.perf_counter() - started
        return CrawlResult(results, stats)

    fetcher = fetch if fetch is not None else HttpFetcher(timeout=timeout)
    if parse is None:
        parse = "template" if fetch is None else None
    registry = TemplateRegistry() if parse == "template" else None

    semaphores = {}
    semaphores_lock = threading.Lock()

    def semaphore_for(url):
        parts = urlsplit(url)
        key = (parts.scheme, parts.hostname, parts.port)
        with semaphores_lock:
            sem = semaphores.get(key)
            if sem is None:
                sem = semaphores[key] = threading.Semaphore(max(1, max_per_host))
            return sem

    def work(index):
        url = urls[index]
        item_started = time.perf_counter()
        attempts = 0
        with semaphore_for(url):
            while True:
                attempts += 1
                try:
                    fetch_started = time.perf_counter()
                    html = fetcher(url)
                    stats.add_fetch_seconds(time.perf_counter() - fetch_started)
                except Exception as exc:  # 统一分类记录，不让单页拖垮批次
                    bucket, retryable, reason = _classify(exc)
                    if retryable and attempts <= retries:
                        logger.info("retry %s: %s (attempt %d)", url, reason, attempts)
                        if backoff:
                            time.sleep(min(backoff * attempts, 1.0))
                        continue
                    stats.record(bucket, url, reason, attempts,
                                 time.perf_counter() - item_started)
                    return
                break

        if html is None:
            stats.record("skipped", url, "empty response", attempts,
                         time.perf_counter() - item_started)
            return
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")

        parse_started = time.perf_counter()
        try:
            if registry is not None:
                signature, fields = registry.parse(html)
                results[index] = fields
                detail = f"template {signature}"
            elif parse is not None:
                results[index] = parse(html)
                detail = "parsed"
            else:
                results[index] = html
                detail = "fetched"
        except ParseError as exc:
            stats.record("failed", url, f"parse error: {exc}", attempts,
                         time.perf_counter() - item_started)
            return
        finally:
            stats.add_parse_seconds(time.perf_counter() - parse_started)
        stats.record("succeeded", url, detail, attempts,
                     time.perf_counter() - item_started)

    workers = min(len(urls), max(_MAX_WORKERS, max_per_host))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, range(len(urls))))
    finally:
        if fetch is None:
            fetcher.close()

    if registry is not None:
        stats.templates = registry.groups
        stats.rules_built = registry.rules_built
    stats.wall_seconds = time.perf_counter() - started
    logger.info("crawl finished: %s", stats.summary())
    logger.debug("crawl details:\n%s", stats.details())
    return CrawlResult(results, stats)
