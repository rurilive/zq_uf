"""请求缓存：键含方法/URL/关键头，按状态码过期，字节容量LRU，多进程共享索引。"""

import contextlib
import fcntl
import hashlib
import json
import os
import pickle
import tempfile
import time

_DEFAULT_DIR = os.path.join(tempfile.gettempdir(), "reqcache")
_VARY_HEADERS = ("accept", "accept-encoding", "authorization", "content-type")
_DEFAULT_CAPACITY = 64 * 1024 * 1024
_DEFAULT_TTL = 300.0
_FALLBACK_TTL = 60.0
_STATUS_TTL = {
    200: 300.0,
    203: 300.0,
    204: 300.0,
    301: 3600.0,
    302: 60.0,
    308: 3600.0,
    404: 30.0,  # 负缓存
    410: 60.0,
}

_capacity = _DEFAULT_CAPACITY
_ttl_map = dict(_STATUS_TTL)

_EMPTY_STATS = {"hits": 0, "misses": 0, "evictions": 0, "expirations": 0}


def make_key(method, url, headers=None):
    """方法大写 + URL + 关键头（名字小写、按名排序），其余头不进键。"""
    parts = [str(method).upper(), str(url)]
    if headers:
        for name, value in sorted(headers.items()):
            name = name.lower()
            if name in _VARY_HEADERS:
                parts.append("{}={}".format(name, str(value).strip()))
    return " ".join(parts)


def configure(capacity_bytes=None, ttl_map=None):
    """调整容量（字节）和状态码TTL（秒），返回当前配置。"""
    global _capacity
    if capacity_bytes is not None:
        _capacity = int(capacity_bytes)
    if ttl_map is not None:
        _ttl_map.update({int(code): float(ttl) for code, ttl in ttl_map.items()})
    return {"capacity_bytes": _capacity, "ttl_map": dict(_ttl_map)}


def clear():
    with _locked() as directory:
        data_dir = os.path.join(directory, "data")
        if os.path.isdir(data_dir):
            for name in os.listdir(data_dir):
                os.unlink(os.path.join(data_dir, name))
        _save(directory, {"entries": {}, "stats": dict(_EMPTY_STATS)})


def get(key):
    key = _normalize(key)
    now = time.time()
    with _locked() as directory:
        index = _load(directory)
        digest = _hash(key)
        entry = index["entries"].get(digest)
        if entry is None:
            index["stats"]["misses"] += 1
            _save(directory, index)
            return None
        if entry["expires"] <= now:
            _remove(directory, index, digest)
            index["stats"]["expirations"] += 1
            index["stats"]["misses"] += 1
            _save(directory, index)
            return None
        entry["accessed"] = now
        index["stats"]["hits"] += 1
        with open(_body_path(directory, entry["file"]), "rb") as handle:
            resp = pickle.load(handle)
        _save(directory, index)
        return resp


def put(key, resp):
    """按状态码定TTL，超容量时淘汰最久未用条目，返回条目数。"""
    key = _normalize(key)
    now = time.time()
    status = resp.get("status") if isinstance(resp, dict) else None
    ttl = _ttl_for(status)
    digest = _hash(key)
    with _locked() as directory:
        index = _load(directory)
        old = index["entries"].pop(digest, None)
        if old is not None:
            _delete_body(directory, old["file"])
        if ttl <= 0:
            _save(directory, index)
            return len(index["entries"])
        _purge_expired(directory, index, now)
        data_dir = os.path.join(directory, "data")
        os.makedirs(data_dir, exist_ok=True)
        with open(_body_path(directory, digest), "wb") as handle:
            pickle.dump(resp, handle)
        index["entries"][digest] = {
            "key": key,
            "file": digest,
            "size": _resp_size(resp),
            "expires": now + ttl,
            "accessed": now,
        }
        _evict(directory, index)
        _save(directory, index)
        return len(index["entries"])


def stats():
    with _locked() as directory:
        index = _load(directory)
        counters = index["stats"]
        return {
            "hits": counters["hits"],
            "misses": counters["misses"],
            "size": len(index["entries"]),
        }


def report():
    """完整指标：命中率、淘汰数、过期数、回源数。"""
    with _locked() as directory:
        index = _load(directory)
        counters = index["stats"]
        total = counters["hits"] + counters["misses"]
        return {
            "hits": counters["hits"],
            "misses": counters["misses"],
            "size": len(index["entries"]),
            "bytes": sum(entry["size"] for entry in index["entries"].values()),
            "capacity_bytes": _capacity,
            "hit_rate": counters["hits"] / total if total else 0.0,
            "evictions": counters["evictions"],
            "expirations": counters["expirations"],
            "fetches": counters["misses"],
        }


def _normalize(key):
    if isinstance(key, str):
        return key
    if isinstance(key, (tuple, list)):
        return make_key(*key)
    if isinstance(key, dict):
        return make_key(key.get("method", "GET"), key["url"], key.get("headers"))
    raise TypeError("unsupported cache key: {!r}".format(key))


def _ttl_for(status):
    if status is None:
        return _DEFAULT_TTL
    if status in _ttl_map:
        return _ttl_map[status]
    if 500 <= status <= 599:
        return 0.0
    return _FALLBACK_TTL


def _resp_size(resp):
    body = resp.get("body", b"") if isinstance(resp, dict) else resp
    if isinstance(body, bytes):
        return len(body)
    if isinstance(body, str):
        return len(body.encode("utf-8"))
    return len(repr(body).encode("utf-8"))


def _hash(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _directory():
    path = os.environ.get("REQCACHE_DIR", _DEFAULT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _body_path(directory, name):
    return os.path.join(directory, "data", name + ".pkl")


@contextlib.contextmanager
def _locked():
    directory = _directory()
    with open(os.path.join(directory, "index.lock"), "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load(directory):
    try:
        with open(os.path.join(directory, "index.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"entries": {}, "stats": dict(_EMPTY_STATS)}


def _save(directory, index):
    tmp_path = os.path.join(directory, "index.json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle)
    os.replace(tmp_path, os.path.join(directory, "index.json"))


def _delete_body(directory, name):
    try:
        os.unlink(_body_path(directory, name))
    except OSError:
        pass


def _remove(directory, index, digest):
    entry = index["entries"].pop(digest, None)
    if entry is not None:
        _delete_body(directory, entry["file"])


def _purge_expired(directory, index, now):
    for digest, entry in list(index["entries"].items()):
        if entry["expires"] <= now:
            _remove(directory, index, digest)
            index["stats"]["expirations"] += 1


def _evict(directory, index):
    entries = index["entries"]
    while entries and sum(entry["size"] for entry in entries.values()) > _capacity:
        oldest = min(entries, key=lambda digest: entries[digest]["accessed"])
        _remove(directory, index, oldest)
        index["stats"]["evictions"] += 1
