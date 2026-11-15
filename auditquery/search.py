"""审计检索：流式过滤、键集游标分页、时间倒序。

- 查询条件：精确字段（user/op/type 等）+ 时间范围 start/end（作用在 ts）。
- 游标：带查询条件、位置标记（最后一条的 (ts, seq)）和数据指纹；
  翻页时数据变化置 stale 提示，键集定位保证新数据插入也不重不漏。
- 索引不可用时自动退回扫描路径，结果一致；页大小与命中上限可配。
"""

from __future__ import annotations

import bisect
import hashlib
import heapq

_events = []        # [((ts, seq), item)]，seq 单调递增，保证键唯一
_seq = 0
_index = None       # 按 (ts, seq) 升序的 [(key, item)]，懒构建
_index_dirty = True

_config = {
    "page_size": 10,        # 默认页大小
    "max_page_size": 500,   # 页大小上限
    "max_hits": 100_000,    # 单次检索命中上限，超过记 truncated
    "use_index": True,      # 置 False 或构建失败时退回扫描
}


def configure(**kw):
    """调整上限：page_size / max_page_size / max_hits / use_index。"""
    unknown = set(kw) - set(_config)
    if unknown:
        raise KeyError(f"unknown config: {sorted(unknown)}")
    _config.update(kw)
    return dict(_config)


def load(events):
    """载入审计事件（替换式），只存 (键, 记录)，键 = (ts, 序号)。"""
    global _seq, _index_dirty
    _events[:] = [((item.get("ts", 0), _seq + i), dict(item))
                  for i, item in enumerate(events)]
    _seq += len(events)
    _index_dirty = True
    return {"loaded": len(_events)}


def search(query=None, size=None):
    """流式过滤，返回第一页：命中数、下一页游标、是否截断。"""
    size = _page_size(size)
    hits, last_key, total, truncated, used_index = _run(query or {}, None, size)
    return _result(query or {}, hits, last_key, size, total, truncated,
                   used_index, stale=False)


def page(cursor):
    """按游标翻页；指纹不符时 stale=True 提示数据已变化。"""
    cursor = cursor or {}
    query = cursor.get("query") or {}
    last = cursor.get("last")
    size = _page_size(cursor.get("size"))
    stale = cursor.get("fp") is not None and cursor["fp"] != _fingerprint()
    hits, last_key, total, truncated, used_index = _run(
        query, tuple(last) if last else None, size)
    return _result(query, hits, last_key, size, total, truncated,
                   used_index, stale)


def _result(query, hits, last_key, size, total, truncated, used_index, stale):
    return {
        "hits": hits,
        "total": total,
        "cursor": _cursor(query, last_key, size) if len(hits) == size else None,
        "truncated": truncated,
        "stale": stale,
        "index": used_index,
    }


def _cursor(query, last_key, size):
    return {"query": query, "last": list(last_key), "size": size,
            "fp": _fingerprint()}


def _run(query, last_key, size):
    """流式执行一页，返回 (hits, last_key, total, truncated, used_index)。

    两条路径都只物化一页：索引路径沿有序索引倒序走；扫描路径单遍
    用大小为 size 的堆取页、同时计数，额外内存 O(页大小)。
    """
    cap = _config["max_hits"]
    idx = _ensure_index()
    if idx is not None:
        hits, last = [], None
        start = len(idx) if last_key is None else bisect.bisect_left(
            idx, (last_key,))
        for i in range(start - 1, -1, -1):
            key, item = idx[i]
            if _matches(item, query):
                hits.append(dict(item))
                last = key
                if len(hits) >= size:
                    break
        total, truncated = _count(query, cap)
        return hits, last, total, truncated, True
    heap = []
    total, truncated = 0, False
    for key, item in _events:
        if not _matches(item, query):
            continue
        total += 1
        if total > cap:
            total, truncated = cap, True
            break
        if last_key is None or key < last_key:
            entry = (key, item)
            if len(heap) < size:
                heapq.heappush(heap, entry)
            elif key > heap[0][0]:
                heapq.heapreplace(heap, entry)
    page_items = sorted(heap, reverse=True)  # 键唯一，不会比较到 item
    hits = [dict(item) for _, item in page_items]
    last = page_items[-1][0] if page_items else None
    return hits, last, total, truncated, False


def _count(query, cap):
    total = 0
    for _, item in _events:
        if _matches(item, query):
            total += 1
            if total > cap:
                return cap, True
    return total, False


def _matches(item, query):
    for k, v in query.items():
        if k == "start":
            if item.get("ts", 0) < v:
                return False
        elif k == "end":
            if item.get("ts", 0) > v:
                return False
        elif item.get(k) != v:
            return False
    return True


def _ensure_index():
    global _index, _index_dirty
    if not _config["use_index"]:
        return None
    if _index_dirty or _index is None:
        try:
            _index = sorted(_events, key=lambda pair: pair[0])
        except Exception:
            _index = None
        _index_dirty = False
    return _index


def _fingerprint():
    return hashlib.sha1(f"{len(_events)}:{_seq}".encode()).hexdigest()[:12]


def _page_size(size):
    if size is None:
        return _config["page_size"]
    return max(1, min(int(size), _config["max_page_size"]))
