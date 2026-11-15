"""采集页面的条件请求缓存。

保存 ETag、Last-Modified 与内容指纹（SHA-256），下一轮采集通过
headers(url) 带上 If-None-Match / If-Modified-Since 条件头；304 只刷新
抓取时间，不覆盖已缓存内容。容量按“单站点条数 + 全局总条数”双重限制，
超限时淘汰最久未用（LRU）的条目。仅使用 Python 标准库。
"""

import hashlib
import time
from urllib.parse import urlsplit

DEFAULT_MAX_PER_SITE = 100
DEFAULT_MAX_ENTRIES = 1000

_IF_NONE_MATCH = "If-None-Match"
_IF_MODIFIED_SINCE = "If-Modified-Since"


def _fingerprint(body):
    """计算内容指纹，str 与 bytes 的 body 都支持。"""
    if isinstance(body, bytes):
        data = body
    else:
        data = str("" if body is None else body).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _site_of(url):
    return urlsplit(url).netloc or url


def _header(headers, name):
    """大小写不敏感地取响应头。"""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


class Cache:
    """条件请求缓存实例。

    max_per_site: 单个站点（netloc）最多缓存条数，超限按 LRU 淘汰。
    max_entries:  全局最多缓存条数，超限按全局 LRU 淘汰。
    time_func:    抓取时间时钟，默认 time.time，测试可注入固定时钟。
    """

    def __init__(self, max_per_site=DEFAULT_MAX_PER_SITE,
                 max_entries=DEFAULT_MAX_ENTRIES, time_func=time.time):
        if max_per_site <= 0 or max_entries <= 0:
            raise ValueError("max_per_site 和 max_entries 必须为正整数")
        self.max_per_site = max_per_site
        self.max_entries = max_entries
        self._time = time_func
        self._entries = {}          # url -> 缓存条目
        self._sites = {}            # site -> set(url)
        self._tick = 0              # 单调 LRU 时钟，越大越近使用
        self._stats = {"hits": 0, "misses": 0, "not_modified": 0,
                       "updated": 0, "changed": 0, "evicted": 0}

    def _next_tick(self):
        self._tick += 1
        return self._tick

    def headers(self, url):
        """返回下一轮请求应带的条件头；无缓存或无校验头时返回 {}。"""
        entry = self._entries.get(url)
        if entry is None:
            return {}
        # 取条件头也算一次使用，刷新 LRU 顺序
        entry["tick"] = self._next_tick()
        conditional = {}
        if entry["etag"]:
            conditional[_IF_NONE_MATCH] = entry["etag"]
        if entry["last_modified"]:
            conditional[_IF_MODIFIED_SINCE] = entry["last_modified"]
        return conditional

    def get(self, url):
        """查看缓存条目（副本），未缓存返回 None。"""
        entry = self._entries.get(url)
        return dict(entry) if entry is not None else None

    def store(self, url, response):
        """写入一次采集响应，返回命中/304/更新/淘汰事件信息。"""
        status = response.get("status", 200)
        resp_headers = response.get("headers") or {}
        existing = self._entries.get(url)
        hit = existing is not None
        evicted_urls = []
        not_modified = False
        updated = False
        changed = False
        stored = False

        if status == 304:
            if hit:
                # 304：只刷新抓取时间与 LRU，内容和校验头原样保留
                existing["fetched_at"] = self._time()
                existing["tick"] = self._next_tick()
                not_modified = True
                stored = True
                self._stats["hits"] += 1
                self._stats["not_modified"] += 1
            # 本地无缓存却收到 304：没有内容可还原，忽略
        elif 200 <= status < 300:
            body = response.get("body", "")
            etag = _header(resp_headers, "ETag")
            last_modified = _header(resp_headers, "Last-Modified")
            fingerprint = _fingerprint(body)
            now = self._time()
            changed = (existing["fingerprint"] != fingerprint) if hit else True
            entry = {
                "url": url,
                "site": _site_of(url),
                "body": body,
                "etag": etag,
                "last_modified": last_modified,
                "fingerprint": fingerprint,
                "created_at": existing["created_at"] if hit else now,
                "fetched_at": now,
                "tick": self._next_tick(),
            }
            self._entries[url] = entry
            self._sites.setdefault(entry["site"], set()).add(url)
            updated = True
            stored = True
            self._stats["hits" if hit else "misses"] += 1
            self._stats["updated"] += 1
            if changed:
                self._stats["changed"] += 1
            evicted_urls = self._enforce_limits(entry["site"])
            self._stats["evicted"] += len(evicted_urls)
        # 其他状态码（如 5xx）不污染已有缓存

        return {
            "url": url,
            "status": status,
            "stored": stored,
            # 兼容旧接口字段：store 处理的是网络响应，不是纯缓存应答
            "cached": False,
            "hit": hit,                  # 命中：store 前已有该 URL 缓存
            "not_modified": not_modified,  # 304：校验通过，内容未动
            "updated": updated,          # 更新：2xx 写入/刷新了内容
            "changed": changed,          # 内容指纹与旧缓存不同
            "evicted": len(evicted_urls),   # 本次淘汰条数
            "evicted_urls": evicted_urls,   # 被淘汰的 URL
        }

    def _enforce_limits(self, site):
        evicted = []
        bucket = self._sites.get(site)
        while bucket and len(bucket) > self.max_per_site:
            evicted.append(self._evict_one(bucket))
        while len(self._entries) > self.max_entries:
            evicted.append(self._evict_one(self._entries.keys()))
        return evicted

    def _evict_one(self, candidates):
        """淘汰候选集合中最久未用（tick 最小）的条目。"""
        url = min(candidates, key=lambda u: self._entries[u]["tick"])
        entry = self._entries.pop(url)
        bucket = self._sites.get(entry["site"])
        if bucket is not None:
            bucket.discard(url)
            if not bucket:
                del self._sites[entry["site"]]
        return url

    def stats(self):
        """累计事件计数与当前容量。"""
        data = dict(self._stats)
        data["entries"] = len(self._entries)
        data["sites"] = len(self._sites)
        return data


# 默认模块级缓存，保持 headers(url)/store(url, response) 的函数接口
_cache = Cache()


def headers(url):
    return _cache.headers(url)


def store(url, response):
    return _cache.store(url, response)


def get(url):
    """查看默认缓存中的条目（副本），未缓存返回 None。"""
    return _cache.get(url)


def reset(max_per_site=DEFAULT_MAX_PER_SITE, max_entries=DEFAULT_MAX_ENTRIES):
    """清空并重建默认缓存（主要供测试和演示使用）。"""
    global _cache
    _cache = Cache(max_per_site=max_per_site, max_entries=max_entries)
    return _cache
