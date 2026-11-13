"""条件请求 + 可共享缓存的采集器封装。

- 保存 ETag / Last-Modified，下次自动带 If-None-Match / If-Modified-Since。
- 304 视为成功，直接返回缓存内容。
- 缓存键包含方法、URL 和关键请求头（KEY_HEADERS）。
- 缓存落盘，多进程通过 flock + 原子替换共享，按字节容量 LRU 淘汰。
- 有过期时间的条目过期后先返回旧值，后台线程条件刷新。
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

try:
    import fcntl
except ImportError:  # 无 flock 的平台退化为仅原子替换
    fcntl = None

KEY_HEADERS = ("accept", "accept-encoding", "accept-language", "authorization", "cookie")
DEFAULT_CAPACITY_BYTES = 64 * 1024 * 1024

_opener = None
_cache = None
_state_lock = threading.Lock()
_inflight = set()


def set_opener(func):
    """测试和 demo 用本地实现替换真实请求；传 None 恢复 urllib 默认实现。"""
    global _opener
    _opener = func


def set_cache(cache):
    """替换全局缓存；传 None 恢复默认的共享文件缓存。"""
    global _cache
    _cache = cache


def fetch(url, headers=None):
    """GET 条件请求；命中缓存按新鲜度返回，过期先给旧值再后台刷新。"""
    return _fetch(_get_cache(), _get_opener(), url, headers)


def _cache_key(method, url, headers):
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    parts = [method.upper(), url]
    for name in KEY_HEADERS:
        if name in lowered:
            parts.append("%s=%s" % (name, lowered[name]))
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def _header(headers, name):
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return value
    return None


def _parse_freshness(response_headers, now):
    """返回 (cacheable, fresh_until)；fresh_until 为 None 表示每次都需条件回源。"""
    control = (_header(response_headers, "cache-control") or "").lower()
    if "no-store" in control:
        return False, None
    for part in control.split(","):
        part = part.strip()
        if part.startswith("max-age="):
            try:
                return True, now + int(part[len("max-age="):])
            except ValueError:
                pass
    expires = _header(response_headers, "expires")
    if expires:
        try:
            return True, parsedate_to_datetime(expires).timestamp()
        except (TypeError, ValueError):
            pass
    return True, None


def _text(body):
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return body


class Cache:
    """可共享的缓存层；path 为 None 时仅进程内存，否则多进程共享该文件。"""

    def __init__(self, path=None, capacity_bytes=DEFAULT_CAPACITY_BYTES):
        self.path = path
        self.capacity_bytes = capacity_bytes
        self._memory = {}
        self._memory_lock = threading.Lock()

    @contextmanager
    def _locked(self):
        if self.path is None:
            with self._memory_lock:
                yield
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with open(self.path + ".lock", "a") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _read(self):
        if self.path is None:
            return {key: dict(entry) for key, entry in self._memory.items()}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _write(self, entries):
        if self.path is None:
            self._memory = entries
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entries, fh)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key):
        with self._locked():
            return self._read().get(key)

    def store(self, key, entry):
        with self._locked():
            entries = self._read()
            entries[key] = entry
            self._evict(entries)
            self._write(entries)

    def touch(self, key, now=None):
        now = time.time() if now is None else now
        with self._locked():
            entries = self._read()
            if key in entries:
                entries[key]["accessed"] = now
                self._write(entries)

    def revalidated(self, key, response_headers, now=None):
        """304 后刷新访问时间，并按响应头续期。"""
        now = time.time() if now is None else now
        with self._locked():
            entries = self._read()
            entry = entries.get(key)
            if entry is None:
                return
            entry["accessed"] = now
            _, fresh_until = _parse_freshness(response_headers, now)
            if fresh_until is not None:
                entry["fresh_until"] = fresh_until
            self._write(entries)

    def _evict(self, entries):
        total = sum(entry.get("size", 0) for entry in entries.values())
        while entries and total > self.capacity_bytes:
            victim = min(entries, key=lambda k: entries[k].get("accessed", 0))
            total -= entries[victim].get("size", 0)
            del entries[victim]


def _store(cache, key, url, request_headers, body, response_headers):
    now = time.time()
    cacheable, fresh_until = _parse_freshness(response_headers, now)
    if not cacheable:
        return
    cache.store(key, {
        "method": "GET",
        "url": url,
        "request_headers": {k: v for k, v in request_headers.items()
                            if k.lower() in KEY_HEADERS},
        "body": body,
        "etag": _header(response_headers, "etag"),
        "last_modified": _header(response_headers, "last-modified"),
        "fresh_until": fresh_until,
        "size": len(body.encode("utf-8")),
        "accessed": now,
        "updated": now,
    })


def _revalidate(cache, opener, key, url, headers, entry):
    request = dict(headers)
    if entry.get("etag"):
        request.setdefault("If-None-Match", entry["etag"])
    if entry.get("last_modified"):
        request.setdefault("If-Modified-Since", entry["last_modified"])
    status, body, response_headers = opener(url, request)
    if status == 304:
        cache.revalidated(key, response_headers)
        return entry["body"]
    body = _text(body)
    if status == 200:
        _store(cache, key, url, headers, body, response_headers)
    return body


def _refresh_in_background(cache, opener, key, url, headers):
    token = (id(cache), key)
    with _state_lock:
        if token in _inflight:
            return
        _inflight.add(token)

    def run():
        try:
            entry = cache.get(key)
            if entry is not None:
                _revalidate(cache, opener, key, url, headers, entry)
        except Exception:
            pass
        finally:
            with _state_lock:
                _inflight.discard(token)

    threading.Thread(target=run, daemon=True).start()


def _fetch(cache, opener, url, headers):
    headers = dict(headers or {})
    key = _cache_key("GET", url, headers)
    entry = cache.get(key)
    if entry is not None:
        fresh_until = entry.get("fresh_until")
        if fresh_until is None:
            # 无新鲜度信息：同步条件回源。
            return _revalidate(cache, opener, key, url, headers, entry)
        cache.touch(key)
        if time.time() < fresh_until:
            return entry["body"]
        # 已过期：先返回旧值，后台条件刷新。
        _refresh_in_background(cache, opener, key, url, headers)
        return entry["body"]
    status, body, response_headers = opener(url, headers)
    body = _text(body)
    if status == 200:
        _store(cache, key, url, headers, body, response_headers)
    return body


def _default_cache():
    base = os.environ.get("CONDFETCH_CACHE_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache", "condfetch")
    return Cache(os.path.join(base, "cache.json"))


def _get_cache():
    global _cache
    with _state_lock:
        if _cache is None:
            _cache = _default_cache()
        return _cache


def _urllib_opener(url, headers):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return (response.status, response.read().decode("utf-8", "replace"),
                    dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, "", dict(exc.headers.items())
        raise


def _get_opener():
    return _opener if _opener is not None else _urllib_opener
