"""链接归一化：规则管线生成规范键，并查集维护等价类，索引可持久化。"""

from __future__ import annotations

import json
import os
import tempfile
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

__all__ = [
    "UrlCanonError",
    "configure",
    "explain",
    "merge",
    "merge_log",
    "normalize",
    "representative",
    "rule_hits",
    "same",
]


class UrlCanonError(ValueError):
    """URL 无法归一化或索引读写失败时抛出，信息明确指出原因。"""


_DEFAULT_PORTS = {"http": 80, "https": 443}
_SUPPORTED_SCHEMES = frozenset(_DEFAULT_PORTS)
_INDEX_PAGES = ("index.html", "index.htm")
_TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "spm", "_ga"}
)
_TRACKING_PREFIXES = ("utm_",)
_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_DEFAULT_INDEX = "urlcanon-index.json"


def _normalize_percent(text, what):
    """解码非保留字符，其余转义的十六进制统一大写；非法编码报错。"""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%":
            if i + 2 >= len(text) or text[i + 1] not in _HEX_DIGITS or text[i + 2] not in _HEX_DIGITS:
                raise UrlCanonError("%s含有非法百分号编码: %r" % (what, text[i : i + 3]))
            pair = text[i + 1 : i + 3]
            decoded = chr(int(pair, 16))
            if decoded in _UNRESERVED:
                out.append(decoded)
            else:
                out.append("%" + pair.upper())
            i += 3
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _remove_dot_segments(path):
    """RFC 3986 5.2.4 点段移除。"""
    output = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if output:
                output.pop()
            continue
        output.append(segment)
    if path.endswith(("/.", "/..")):
        output.append("")
    return "/".join(output)


def _build_query(pairs):
    return "&".join(quote(k, safe="") + "=" + quote(v, safe="") for k, v in pairs)


def _is_tracking(name):
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def _rule_case(parts):
    old = (parts["scheme"], parts["host"])
    parts["scheme"] = parts["scheme"].lower()
    parts["host"] = parts["host"].lower()
    if (parts["scheme"], parts["host"]) != old:
        return "scheme/主机名大小写归一"


def _rule_default_port(parts):
    port = parts["port"]
    if port is not None and port == _DEFAULT_PORTS.get(parts["scheme"]):
        parts["port"] = None
        return "移除默认端口 :%d" % port


def _rule_percent(parts):
    path = _normalize_percent(parts["path"], "路径")
    query = _normalize_percent(parts["query"], "查询串")
    changed = (path, query) != (parts["path"], parts["query"])
    parts["path"], parts["query"] = path, query
    if changed:
        return "百分号编码归一(解码非保留字符, 十六进制大写)"


def _rule_default_index(parts):
    for name in _INDEX_PAGES:
        if parts["path"].endswith("/" + name):
            parts["path"] = parts["path"][: -len(name)]
            return "移除默认首页 %s" % name


def _rule_dot_segments(parts):
    path = _remove_dot_segments(parts["path"])
    if path != parts["path"]:
        parts["path"] = path
        return "移除路径点段"


def _rule_fragment(parts):
    if parts["fragment"]:
        fragment = parts["fragment"]
        parts["fragment"] = ""
        return "丢弃 fragment #%s" % fragment


def _rule_tracking(parts):
    if not parts["query"]:
        return None
    pairs = parse_qsl(parts["query"], keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not _is_tracking(k)]
    if len(kept) != len(pairs):
        removed = sorted({k for k, _ in pairs if _is_tracking(k)})
        parts["query"] = _build_query(kept)
        return "移除追踪参数: %s" % ", ".join(removed)


def _rule_query_sort(parts):
    if not parts["query"]:
        return None
    pairs = parse_qsl(parts["query"], keep_blank_values=True)
    rebuilt = _build_query(sorted(pairs, key=lambda kv: (kv[0], kv[1])))
    if rebuilt != parts["query"]:
        parts["query"] = rebuilt
        return "查询参数按键值排序"


# 优先级高的先应用；冲突时以高优先级结果为准，
# 低优先级规则只能在其输出上继续加工，不得复活已删除的内容。
_RULES = [
    (100, "case", _rule_case),
    (90, "default-port", _rule_default_port),
    (80, "percent-encoding", _rule_percent),
    (70, "default-index", _rule_default_index),
    (60, "dot-segments", _rule_dot_segments),
    (50, "fragment", _rule_fragment),
    (40, "tracking-params", _rule_tracking),
    (30, "query-sort", _rule_query_sort),
]


def _canonical_key(url):
    if not isinstance(url, str):
        raise UrlCanonError("url 必须是字符串, 收到 %s" % type(url).__name__)
    stripped = url.strip()
    hits = []
    if stripped != url:
        hits.append({"rule": "trim", "priority": 110, "detail": "去除首尾空白"})
    try:
        split = urlsplit(stripped)
    except ValueError as exc:
        raise UrlCanonError("URL 解析失败 %r: %s" % (stripped, exc)) from exc
    if not split.scheme:
        raise UrlCanonError("缺少 scheme: %r" % stripped)
    if split.scheme.lower() not in _SUPPORTED_SCHEMES:
        raise UrlCanonError("不支持的 scheme %r: %r" % (split.scheme, stripped))
    if not split.hostname:
        raise UrlCanonError("缺少主机名: %r" % stripped)
    try:
        port = split.port
    except ValueError as exc:
        raise UrlCanonError("非法端口 %r: %s" % (stripped, exc)) from exc
    # urlsplit 已把 scheme/hostname 转小写，这里取原始写法以便记录大小写命中
    raw_scheme = stripped.split(":", 1)[0]
    raw_host = split.netloc.rsplit("@", 1)[-1]
    if raw_host.startswith("["):
        raw_host = raw_host[1 : raw_host.index("]")]
    else:
        raw_host = raw_host.split(":")[0]
    parts = {
        "scheme": raw_scheme,
        "host": raw_host,
        "port": port,
        "path": split.path or "/",
        "query": split.query,
        "fragment": split.fragment,
    }
    for priority, name, rule in _RULES:
        detail = rule(parts)
        if detail:
            hits.append({"rule": name, "priority": priority, "detail": detail})
    netloc = parts["host"] if parts["port"] is None else "%s:%d" % (parts["host"], parts["port"])
    key = urlunsplit((parts["scheme"], netloc, parts["path"], parts["query"], ""))
    return key, hits


class _Store:
    """并查集 + 持久化。类代表 = 类中注册序号最小的键，序号落盘，代表不漂移。"""

    def __init__(self, path=None):
        self._path = path
        self._parent = {}
        self._order = {}
        self._merges = []
        self._counter = 0
        if path is not None and os.path.exists(path):
            self._load(path)

    def _load(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            parent, order = data["parent"], data["order"]
            merges, counter = data["merges"], data["counter"]
            if not isinstance(parent, dict) or not isinstance(order, dict):
                raise TypeError("parent/order 必须是对象")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise UrlCanonError("索引文件损坏 %s: %s" % (path, exc)) from exc
        self._parent = {str(k): str(v) for k, v in parent.items()}
        self._order = {str(k): int(v) for k, v in order.items()}
        self._merges = list(merges)
        self._counter = int(counter)

    def _save(self):
        if self._path is None:
            return
        payload = {
            "version": 1,
            "counter": self._counter,
            "order": self._order,
            "parent": self._parent,
            "merges": self._merges,
        }
        directory = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError as exc:
            raise UrlCanonError("索引写入失败 %s: %s" % (self._path, exc)) from exc

    def register(self, key):
        if key not in self._parent:
            self._parent[key] = key
            self._order[key] = self._counter
            self._counter += 1
            self._save()
        return key

    def find(self, key):
        parent = self._parent
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:
            parent[key], key = root, parent[key]
        return root

    def union(self, key_a, key_b):
        self.register(key_a)
        self.register(key_b)
        root_a, root_b = self.find(key_a), self.find(key_b)
        merged = root_a != root_b
        if merged:
            if self._order[root_a] <= self._order[root_b]:
                winner, loser = root_a, root_b
            else:
                winner, loser = root_b, root_a
            self._parent[loser] = winner
            rep = winner
        else:
            rep = root_a
        self._merges.append(
            {"seq": len(self._merges), "a": key_a, "b": key_b, "representative": rep, "merged": merged}
        )
        self._save()
        return rep


_store = None


def _get_store():
    global _store
    if _store is None:
        _store = _Store(os.environ.get("URLCANON_INDEX", _DEFAULT_INDEX))
    return _store


def configure(index_path=None):
    """指定索引文件并加载；index_path 为 None 时仅内存运行、不落盘。"""
    global _store
    _store = _Store(None if index_path is None else os.fspath(index_path))
    return _store


def normalize(url):
    """按规则管线生成规范键，并注册进等价类索引。"""
    key, _hits = _canonical_key(url)
    return _get_store().register(key)


def explain(url):
    """返回 {"key": 规范键, "hits": [规则命中记录]}。"""
    key, hits = _canonical_key(url)
    _get_store().register(key)
    return {"key": key, "hits": hits}


def rule_hits(url):
    """该 URL 命中的规则记录（按优先级从高到低）。"""
    return explain(url)["hits"]


def representative(url):
    """URL 所在等价类的类代表（确定后不再漂移）。"""
    return _get_store().find(normalize(url))


def same(a, b):
    """两个 URL 是否属于同一等价类（同规范键或已合并）。"""
    store = _get_store()
    return store.find(normalize(a)) == store.find(normalize(b))


def merge(a, b):
    """合并两个 URL 的等价类，返回类代表；合并记录持久化。"""
    return _get_store().union(normalize(a), normalize(b))


def merge_log():
    """全部合并记录（含已同类、未实际合并的记录）。"""
    return [dict(record) for record in _get_store()._merges]

