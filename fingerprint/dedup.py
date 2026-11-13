"""两级指纹去重：URL 规范化精确去重 + 正文分片近重复检测，支持本地持久化。"""

import hashlib
import json
import os
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "spm", "_ga"}
)

DEFAULT_THRESHOLD = 0.9
DEFAULT_FLUSH_EVERY = 50
SHINGLE_SIZE = 5
_STORAGE_VERSION = 1


def _is_tracking_param(name):
    name = name.lower()
    return name.startswith("utm_") or name in TRACKING_PARAMS


def normalize_url(url):
    """规范化 URL：小写 scheme/host、去默认端口、去追踪参数、查询参数排序、去 fragment。"""
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        host = "%s:%d" % (host, port)
    path = quote(unquote(parts.path or ""), safe="/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking_param(k)
        )
    )
    return urlunsplit((scheme, host, path, query, ""))


def normalize_text(text):
    """去空白和标点，NFKC 归一并小写，只保留字母和数字。"""
    chars = []
    for ch in unicodedata.normalize("NFKC", text or ""):
        if unicodedata.category(ch)[0] in ("L", "N"):
            chars.append(ch.lower())
    return "".join(chars)


def _hash(gram):
    return int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")


def _shingles(text, size=SHINGLE_SIZE):
    norm = normalize_text(text)
    if not norm:
        return []
    grams = {norm} if len(norm) <= size else {norm[i : i + size] for i in range(len(norm) - size + 1)}
    return sorted(_hash(gram) for gram in grams)


def _jaccard(a, b):
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def digest(record):
    """一级指纹：规范化后的 URL。"""
    return normalize_url(record.get("url", ""))


def content_digest(record):
    """二级指纹：正文去空白标点后分片的哈希列表（升序）。"""
    text = record.get("content") or record.get("text") or ""
    return _shingles(text)


@dataclass
class DedupResult:
    hit: bool
    kind: str = None  # "url" / "content" / None
    similarity: float = 0.0
    first_seen: float = None


class Fingerprint:
    """两级指纹库。内存只保留时间窗口内的条目，按批刷盘到本地 JSON 文件。"""

    def __init__(self, storage_path=None, threshold=DEFAULT_THRESHOLD,
                 window_seconds=None, flush_every=DEFAULT_FLUSH_EVERY, clock=None):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.flush_every = max(1, flush_every)
        self.storage_path = storage_path
        self._clock = clock or time.time
        self._entries = []
        self._url_index = {}
        self._stats = {}
        self._pending = 0
        if storage_path:
            self._load()

    def seen(self, key):
        """一级精确去重；key 为规范化 URL 字符串，传入 dict 时走完整两级流程。"""
        if isinstance(key, dict):
            return self.check(key).hit
        return self._check(normalize_url(key), []).hit

    def check(self, record):
        """两级判重，返回 DedupResult（命中类型、相似度、首次出现时间）。"""
        return self._check(digest(record), content_digest(record))

    def stats(self):
        """按站点返回 checked/url_hits/content_hits/new/hit_rate。"""
        out = {}
        for site, s in self._stats.items():
            hits = s["url_hits"] + s["content_hits"]
            checked = s["checked"]
            out[site] = dict(s, hits=hits, hit_rate=hits / checked if checked else 0.0)
        return out

    def flush(self):
        self._pending = 0
        if not self.storage_path:
            return
        payload = {"version": _STORAGE_VERSION, "entries": self._entries, "stats": self._stats}
        tmp = self.storage_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self.storage_path)

    def close(self):
        self.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _check(self, url_fp, shingles):
        self._evict()
        site = urlsplit(url_fp).netloc or "(local)"
        stats = self._stats.setdefault(site, {"checked": 0, "url_hits": 0, "content_hits": 0, "new": 0})
        stats["checked"] += 1
        entry = self._url_index.get(url_fp)
        if entry is not None:
            stats["url_hits"] += 1
            return DedupResult(True, "url", 1.0, entry["first_seen"])
        best_sim, best = 0.0, None
        if shingles:
            new_set = set(shingles)
            for e in self._entries:
                if not e["shingles"]:
                    continue
                sim = _jaccard(new_set, set(e["shingles"]))
                if sim > best_sim:
                    best_sim, best = sim, e
            if best is not None and best_sim >= self.threshold:
                stats["content_hits"] += 1
                return DedupResult(True, "content", best_sim, best["first_seen"])
        self._add(url_fp, shingles, site)
        stats["new"] += 1
        return DedupResult(False, None, best_sim, None)

    def _add(self, url_fp, shingles, site):
        entry = {"url": url_fp, "shingles": list(shingles), "site": site, "first_seen": self._clock()}
        self._entries.append(entry)
        self._url_index[url_fp] = entry
        self._pending += 1
        if self._pending >= self.flush_every:
            self.flush()

    def _evict(self):
        if self.window_seconds is None:
            return
        cutoff = self._clock() - self.window_seconds
        kept = [e for e in self._entries if e["first_seen"] >= cutoff]
        if len(kept) != len(self._entries):
            self._entries = kept
            self._url_index = {e["url"]: e for e in kept}
            self._pending += 1

    def _load(self):
        try:
            with open(self.storage_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        for entry in data.get("entries", []):
            self._entries.append(entry)
            self._url_index[entry["url"]] = entry
        for site, s in data.get("stats", {}).items():
            self._stats[site] = {k: s.get(k, 0) for k in ("checked", "url_hits", "content_hits", "new")}
        self._evict()
        self._pending = 0
