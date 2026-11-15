"""审计检索与分页：流式扫描、按时间排序、带校验的翻页游标。"""

import hashlib
import json
import time

_events = []        # load 注入的审计事件
_sort_keys = []     # 与 _events 平行的排序键 (时间, 序号)
_time_order = []    # 按排序键升序的序号，扫描按此顺序进行，填满一页即停
_version = 0        # 数据版本，每次 load +1，游标据此发现翻页期间的数据变更
_index = {}         # 字段 -> {值: set(序号)}，懒构建
_index_version = -1

_CONFIG = {
    "page_size": 100,          # 每页条数
    "max_hits": 100000,        # 单次检索命中上限，超出截断
    "cursor_ttl": 300.0,       # 游标有效期（秒），None 表示不过期
    "timeout": None,           # 检索超时（秒），None 表示不限
    "index_fields": ("user", "type"),  # 建索引的等值字段，其余降级扫描
}

_TIME_KEYS = ("time", "ts")    # 事件时间字段，优先 time
_RANGE_KEYS = ("time_from", "time_to")


class CursorExpired(Exception):
    """游标已过期。"""


class CursorInvalid(Exception):
    """游标校验失败（被篡改或结构非法）。"""


class SearchTimeout(Exception):
    """检索超时，已中断。"""


def configure(**options):
    """调整分页大小、命中上限、游标有效期、超时和索引字段。"""
    global _index_version
    for key, value in options.items():
        if key not in _CONFIG:
            raise KeyError("未知配置项: %r" % key)
        _CONFIG[key] = value
    if "index_fields" in options:
        _index_version = -1
    return dict(_CONFIG)


def load(events):
    """注入审计事件（可传生成器），建立时间序，检索时流式扫描。"""
    global _version, _index_version
    _events[:] = list(events)
    _sort_keys[:] = [_sort_key(event, seq) for seq, event in enumerate(_events)]
    _time_order[:] = sorted(range(len(_events)), key=_sort_keys.__getitem__)
    _version += 1
    _index_version = -1
    _index.clear()
    return {"loaded": len(_events)}


def search(query, size=None, fields=None, timeout=None, max_hits=None):
    """流式检索第一页：按时间、操作人、类型过滤，结果按时间排序。

    query 支持等值字段（如 user、type）和时间范围 time_from/time_to（含边界）。
    fields 按字段裁剪命中；命中超过上限时截断并置 truncated=True。
    """
    query = dict(query or {})
    size = int(size or _CONFIG["page_size"])
    limit = min(size, int(max_hits or _CONFIG["max_hits"]))
    hits, last_key, truncated, index_used = _scan(
        query, None, limit, _deadline(timeout), fields)
    return {
        "hits": hits,
        "cursor": _make_cursor(query, last_key, size, fields) if truncated else None,
        "truncated": truncated,
        "page": {
            "size": size,
            "returned": len(hits),
            "has_more": truncated,
            "index_used": index_used,
        },
    }


def page(cursor, timeout=None):
    """翻页：游标带查询条件、位置指纹和校验；数据变更时结果带 warning 提示。"""
    if not cursor:
        raise CursorInvalid("游标为空")
    if "v" not in cursor:  # 兼容旧的偏移量游标
        return _legacy_page(cursor)
    _check_cursor(cursor)
    query = dict(cursor.get("query") or {})
    last = cursor.get("last")
    after = tuple(last) if last is not None else None
    size = int(cursor.get("size") or _CONFIG["page_size"])
    fields = cursor.get("fields")
    limit = min(size, int(_CONFIG["max_hits"]))
    hits, last_key, truncated, index_used = _scan(
        query, after, limit, _deadline(timeout), fields)
    result = {
        "hits": hits,
        "cursor": _make_cursor(query, last_key, size, fields) if truncated else None,
        "truncated": truncated,
        "page": {
            "size": size,
            "returned": len(hits),
            "has_more": truncated,
            "index_used": index_used,
        },
    }
    if cursor.get("version") != _version:
        result["warning"] = "翻页期间数据已变更，结果可能重复或遗漏，建议重新检索"
    return result


def export(query, fields=None, timeout=None):
    """导出：与 search 同一查询口径，流式产出全部命中，按时间排序。"""
    query = dict(query or {})
    candidates = _candidates(query)
    for _key, event in _iter_matches(query, None, _deadline(timeout), candidates):
        yield _project(event, fields)


# ---- 内部实现 ----

def _norm_time(value):
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (0, float(value))
    if value is None:
        return (1, "")
    return (1, str(value))


def _time_of(event):
    for key in _TIME_KEYS:
        if key in event:
            return event[key]
    return None


def _sort_key(event, seq):
    return _norm_time(_time_of(event)) + (seq,)


def _matches(event, query):
    for key, want in query.items():
        if key == "time_from":
            if _norm_time(_time_of(event)) < _norm_time(want):
                return False
        elif key == "time_to":
            if _norm_time(_time_of(event)) > _norm_time(want):
                return False
        elif event.get(key) != want:
            return False
    return True


def _ensure_index():
    global _index_version
    if _index_version == _version:
        return
    _index.clear()
    for field in _CONFIG["index_fields"]:
        _index[field] = {}
    for seq, event in enumerate(_events):
        for field in _CONFIG["index_fields"]:
            if field not in event:
                continue
            try:
                _index[field].setdefault(event[field], set()).add(seq)
            except TypeError:  # 不可哈希的字段值不进索引
                continue
    _index_version = _version


def _candidates(query):
    """等值字段全部有索引时返回候选序号集合，否则返回 None 降级全量扫描。"""
    exact = [k for k in query if k not in _RANGE_KEYS]
    if not exact or any(k not in _CONFIG["index_fields"] for k in exact):
        return None
    _ensure_index()
    sets = []
    try:
        for key in exact:
            bucket = _index.get(key, {}).get(query[key])
            if not bucket:
                return set()
            sets.append(bucket)
    except TypeError:  # 查询值不可哈希，降级扫描
        return None
    return set.intersection(*sets)


def _iter_matches(query, after, deadline, candidates):
    """按时间序流式产出命中，after 之后的第一条开始，支持超时中断。"""
    for scanned, seq in enumerate(_time_order):
        key = _sort_keys[seq]
        if after is not None and key <= after:
            continue
        if deadline is not None and (scanned & 1023) == 0 \
                and time.monotonic() > deadline:
            raise SearchTimeout("检索超时，已中断（已扫描 %d 条）" % scanned)
        if candidates is not None and seq not in candidates:
            continue
        event = _events[seq]
        if _matches(event, query):
            yield key, event


def _scan(query, after, limit, deadline, fields):
    candidates = _candidates(query)
    hits = []
    last_key = after
    truncated = False
    for key, event in _iter_matches(query, after, deadline, candidates):
        if len(hits) >= limit:
            truncated = True  # 还有命中，截断并告知
            break
        hits.append(_project(event, fields))
        last_key = key
    return hits, last_key, truncated, candidates is not None


def _project(event, fields):
    if not fields:
        return dict(event)
    return {k: event[k] for k in fields if k in event}


def _checksum(payload):
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _make_cursor(query, last_key, size, fields):
    ttl = _CONFIG["cursor_ttl"]
    payload = {
        "v": 1,
        "query": query,
        "last": list(last_key) if last_key is not None else None,
        "size": size,
        "fields": list(fields) if fields else None,
        "version": _version,
        "expires": (time.time() + ttl) if ttl else None,
    }
    payload["checksum"] = _checksum(payload)
    return payload


def _check_cursor(cursor):
    expect = _checksum({k: v for k, v in cursor.items() if k != "checksum"})
    if expect != cursor.get("checksum"):
        raise CursorInvalid("游标校验失败")
    expires = cursor.get("expires")
    if expires is not None and time.time() > expires:
        raise CursorExpired("游标已过期，请重新检索")


def _deadline(timeout):
    timeout = _CONFIG["timeout"] if timeout is None else timeout
    if timeout is None:
        return None
    return time.monotonic() + float(timeout)


def _legacy_page(cursor):
    """兼容旧的偏移量游标：按时间序切片。"""
    offset = int(cursor.get("offset", 0))
    size = int(cursor.get("size", _CONFIG["page_size"]))
    seqs = _time_order[offset:offset + size]
    return {
        "hits": [dict(_events[seq]) for seq in seqs],
        "cursor": {"offset": offset + size, "size": size},
        "truncated": False,
        "page": {
            "size": size,
            "returned": len(seqs),
            "has_more": offset + size < len(_events),
            "index_used": False,
        },
    }
