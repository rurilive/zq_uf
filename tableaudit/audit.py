"""表操作审计：按 (表, 操作人, 操作类型) 在小时/天两级窗口聚合。

设计口径
--------
- ``record(event)`` 每次仍只写入一条事件（返回值保持旧口径：
  ``recorded/total/aggregated=False``），聚合在存储与查询层完成。
- 聚合键：``(table, user, op)`` × 窗口粒度（hour/day，UTC 整点桶）。
- 去重：事件剔除时间字段后做 canonical JSON 取 sha256 指纹，同一聚合
  窗口内指纹只计一次 ``unique``，重复部分计入 ``duplicates``。
- 敏感操作：聚合体打 ``sensitive`` 标记，样本照留，原文在独立存储中
  全量保留（不做样本截断）。
- 操作人变更：同一 ``(table, op)`` 的操作人发生变化时单独记录一条
  ``operator_change``。
- 保留期：``configure(retention_seconds=..., archive_dir=...)`` 可配，
  过期聚合体/变更记录归档（内存列表 + 可选 JSONL 文件）。
- 查询：精确匹配 + 时间范围 + 按表/时间下钻，结果按 ``last_seen``
  时间倒序，支持 opaque 游标翻页。

仅使用 Python 标准库。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import time as _time

HOUR = 3600
DAY = 86400

DEFAULT_SENSITIVE_OPS = frozenset(
    {"drop", "truncate", "delete", "alter", "grant", "revoke"}
)
DEFAULT_SAMPLE_LIMIT = 3
DEFAULT_PAGE_LIMIT = 100

# 指纹计算时忽略的时间字段
_TIME_KEYS = ("time", "ts")

_MISSING = object()


class _Config:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sensitive_ops = set(DEFAULT_SENSITIVE_OPS)
        self.retention_seconds = None
        self.archive_dir = None
        self.sample_limit = DEFAULT_SAMPLE_LIMIT
        self.page_limit = DEFAULT_PAGE_LIMIT


_cfg = _Config()

# 原始事件总数（含重复），record() 返回的 total 即此值
_raw_total = 0
# (granularity, table, user, op, bucket) -> 聚合体
_aggregates = {}
# 小时聚合 key -> 敏感操作原文（全量，不截断）
_sensitive_raw = {}
# (table, op) -> 最近一次操作人
_last_user = {}
# 操作人变更记录
_operator_changes = []
# 已归档条目（内存留存，read_archive 可查）
_archive = []


# ----------------------------------------------------------------------
# 配置与状态
# ----------------------------------------------------------------------

def configure(sensitive_ops=None, retention_seconds=None, archive_dir=None,
              sample_limit=None, page_limit=None):
    """配置敏感操作集合、保留期（秒）、归档目录、样本上限、分页上限。

    参数传 ``None`` 表示不修改该项；``retention_seconds=None`` 表示永久保留。
    """
    if sensitive_ops is not None:
        _cfg.sensitive_ops = set(sensitive_ops)
    if retention_seconds is not None:
        _cfg.retention_seconds = retention_seconds
    if archive_dir is not None:
        _cfg.archive_dir = archive_dir
    if sample_limit is not None:
        _cfg.sample_limit = int(sample_limit)
    if page_limit is not None:
        _cfg.page_limit = int(page_limit)
    return _config_snapshot()


def _config_snapshot():
    return {
        "sensitive_ops": sorted(_cfg.sensitive_ops),
        "retention_seconds": _cfg.retention_seconds,
        "archive_dir": _cfg.archive_dir,
        "sample_limit": _cfg.sample_limit,
        "page_limit": _cfg.page_limit,
    }


def reset():
    """清空全部内存状态并恢复默认配置（测试隔离用）。"""
    global _raw_total
    _cfg.reset()
    _raw_total = 0
    _aggregates.clear()
    _sensitive_raw.clear()
    _last_user.clear()
    _operator_changes.clear()
    _archive.clear()


# ----------------------------------------------------------------------
# 指纹 / 时间 / 聚合体工具
# ----------------------------------------------------------------------

def _event_time(event):
    """取事件时间（epoch 秒）；事件未携带时用当前墙钟。"""
    ts = event.get("time", event.get("ts"))
    if ts is None:
        return _time.time()
    return ts


def fingerprint(event):
    """事件指纹：剔除时间字段后的 canonical JSON 的 sha256。"""
    payload = {k: v for k, v in event.items() if k not in _TIME_KEYS}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bucket(ts, size):
    return int(ts) // size


def _new_aggregate(granularity, size, key):
    _, table, user, op, bucket = key
    window_start = bucket * size
    return {
        "table": table,
        "user": user,
        "op": op,
        "granularity": granularity,
        "bucket": bucket,
        "window_start": window_start,
        "window_end": window_start + size,
        "count": 0,
        "unique": 0,
        "duplicates": 0,
        "sensitive": False,
        "raw_count": 0,
        "first_seen": None,
        "last_seen": None,
        "samples": [],
        # 内部去重集合，对外输出时剥离
        "_fps": set(),
    }


def _track_user(table, op, user, ts):
    """跟踪操作人变更，返回变更条目（无变更时返回 None）。"""
    key = (table, op)
    previous = _last_user.get(key, _MISSING)
    entry = None
    if previous is not _MISSING and previous != user:
        entry = {
            "type": "operator_change",
            "table": table,
            "op": op,
            "from_user": previous,
            "to_user": user,
            "time": ts,
        }
        _operator_changes.append(entry)
    _last_user[key] = user
    return entry


# ----------------------------------------------------------------------
# 写入
# ----------------------------------------------------------------------

def record(event):
    """记录一条表操作事件。

    返回值保持旧口径：``recorded``、``total``（原始事件数，含重复）、
    ``aggregated=False``（本调用只写一条事件，聚合在存储层完成），
    另附 ``fingerprint/duplicate/sensitive/operator_change`` 诊断信息。
    """
    global _raw_total
    evt = copy.deepcopy(event)
    ts = _event_time(evt)
    fp = fingerprint(evt)

    table = evt.get("table")
    user = evt.get("user")
    op = evt.get("op")
    is_sensitive = op in _cfg.sensitive_ops

    operator_change = _track_user(table, op, user, ts)

    duplicate = False
    for granularity, size in (("hour", HOUR), ("day", DAY)):
        bucket = _bucket(ts, size)
        key = (granularity, table, user, op, bucket)
        agg = _aggregates.get(key)
        if agg is None:
            agg = _new_aggregate(granularity, size, key)
            _aggregates[key] = agg

        agg["count"] += 1
        if fp in agg["_fps"]:
            agg["duplicates"] += 1
            if granularity == "hour":
                duplicate = True
        else:
            agg["_fps"].add(fp)
            agg["unique"] += 1
            if len(agg["samples"]) < _cfg.sample_limit:
                agg["samples"].append(copy.deepcopy(evt))

        if is_sensitive:
            agg["sensitive"] = True
            agg["raw_count"] += 1
            if granularity == "hour":
                # 敏感原文单独全量保留
                _sensitive_raw.setdefault(key, []).append(copy.deepcopy(evt))

        if agg["first_seen"] is None or ts < agg["first_seen"]:
            agg["first_seen"] = ts
        if agg["last_seen"] is None or ts > agg["last_seen"]:
            agg["last_seen"] = ts

    _raw_total += 1
    # 仅实时写入（事件未自带时间戳）时按墙钟自动归档；
    # 回填历史事件不触发，避免把同批固定时间戳数据提前清走。
    if "time" not in evt and "ts" not in evt:
        archive_expired()

    return {
        "recorded": True,
        "total": _raw_total,
        "aggregated": False,
        "fingerprint": fp,
        "duplicate": duplicate,
        "sensitive": is_sensitive,
        "operator_change": copy.deepcopy(operator_change),
    }


# ----------------------------------------------------------------------
# 保留期与归档
# ----------------------------------------------------------------------

def _public_aggregate(agg, include_raw=False):
    """对外视图：剥离内部字段，按需附敏感原文。"""
    out = {k: v for k, v in agg.items() if not k.startswith("_")}
    out["samples"] = copy.deepcopy(out["samples"])
    key = (agg["granularity"], agg["table"], agg["user"],
           agg["op"], agg["bucket"])
    # 小时桶统一暴露 raw 字段：非敏感或未请求时为 None
    if agg["granularity"] == "hour":
        if include_raw and agg["sensitive"]:
            out["raw"] = copy.deepcopy(_sensitive_raw.get(key, []))
        else:
            out["raw"] = None
    return out


def archive_expired(now=None):
    """归档超过保留期的聚合体与操作人变更，返回本次归档条目列表。

    判定口径：``window_end + retention_seconds <= now``。未配置保留期时
    为空操作。``now`` 可注入（epoch 秒）便于测试。
    """
    retention = _cfg.retention_seconds
    if retention is None:
        return []
    if now is None:
        now = _time.time()

    archived = []
    for key in list(_aggregates.keys()):
        agg = _aggregates[key]
        if agg["window_end"] + retention <= now:
            entry = {
                "kind": "aggregate",
                "archived_at": now,
                "data": _public_aggregate(agg, include_raw=False),
            }
            if agg["granularity"] == "hour" and agg["sensitive"]:
                entry["raw"] = copy.deepcopy(_sensitive_raw.pop(key, []))
            _archive.append(entry)
            archived.append(entry)
            del _aggregates[key]
            _sensitive_raw.pop(key, None)

    kept_changes = []
    for change in _operator_changes:
        if change["time"] + retention <= now:
            entry = {
                "kind": "operator_change",
                "archived_at": now,
                "data": copy.deepcopy(change),
            }
            _archive.append(entry)
            archived.append(entry)
        else:
            kept_changes.append(change)
    _operator_changes[:] = kept_changes

    if archived and _cfg.archive_dir:
        os.makedirs(_cfg.archive_dir, exist_ok=True)
        path = os.path.join(_cfg.archive_dir, "audit-archive.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for entry in archived:
                fh.write(json.dumps(entry, ensure_ascii=False,
                                    default=str) + "\n")
    return archived


def read_archive():
    """返回已归档条目（深拷贝）。"""
    return copy.deepcopy(_archive)


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------

def _encode_cursor(offset):
    token = json.dumps({"o": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii")


def _decode_cursor(cursor):
    try:
        token = base64.urlsafe_b64decode(cursor.encode("ascii"))
        return int(json.loads(token.decode("utf-8")).get("o", 0))
    except Exception:
        return 0


def _matches(agg, exact, start, end):
    if start is not None and agg["window_end"] <= start:
        return False
    if end is not None and agg["window_start"] >= end:
        return False
    for field, value in exact.items():
        if agg.get(field) != value:
            return False
    return True


def _sort_key(agg):
    # 时间倒序；同时间用窗口与聚合键保证确定性
    return (
        agg["last_seen"] if agg["last_seen"] is not None else 0,
        agg["window_start"],
        agg["table"] or "",
        agg["user"] or "",
        agg["op"] or "",
    )


def query(filters=None, limit=None, cursor=None, include_raw=None):
    """按条件查询聚合结果。

    filters 支持：
    - 精确匹配：``table/user/op/sensitive/granularity/bucket`` 等聚合体字段；
    - 时间范围：``start``/``end``（epoch 秒，半开区间，按窗口相交判定）；
    - 翻页：``limit``、``cursor``（也可用同名关键字参数传入）；
    - ``granularity``：``hour``（默认）或 ``day``；
    - ``include_raw``：为真时敏感聚合体附完整原文。

    返回保持旧键 ``hits``/``cursor``，结果按 ``last_seen`` 时间倒序，
    ``cursor`` 为下一页游标（无更多数据时为 ``None``）。
    """
    filters = dict(filters or {})
    granularity = filters.pop("granularity", "hour")
    start = filters.pop("start", None)
    end = filters.pop("end", None)
    include_raw = bool(filters.pop("include_raw", False) or include_raw)
    limit = filters.pop("limit", None) or limit or _cfg.page_limit
    cursor = filters.pop("cursor", None) or cursor

    matched = [
        agg for agg in _aggregates.values()
        if agg["granularity"] == granularity
        and _matches(agg, filters, start, end)
    ]
    matched.sort(key=_sort_key, reverse=True)

    total = len(matched)
    offset = _decode_cursor(cursor) if cursor else 0
    page = matched[offset:offset + limit]
    has_more = offset + len(page) < total
    next_cursor = _encode_cursor(offset + len(page)) if has_more else None

    changes = [
        copy.deepcopy(ch) for ch in _operator_changes
        if (filters.get("table") is None or ch["table"] == filters["table"])
        and (filters.get("op") is None or ch["op"] == filters["op"])
        and (start is None or ch["time"] >= start)
        and (end is None or ch["time"] < end)
    ]
    changes.sort(key=lambda ch: ch["time"], reverse=True)

    return {
        "hits": [_public_aggregate(agg, include_raw) for agg in page],
        # 旧键：下一页游标，末页为 None
        "cursor": next_cursor,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total": total,
        "aggregated": True,
        "granularity": granularity,
        "operator_changes": changes,
    }


def drill_down(table, day=None, user=None, op=None):
    """按表（可再按某天）下钻到小时级聚合，时间倒序返回。

    ``day`` 可传 epoch 秒（自动取其所在天桶）或直接传天桶编号；
    不传则返回该表全部小时聚合。
    """
    exact = {"table": table}
    if user is not None:
        exact["user"] = user
    if op is not None:
        exact["op"] = op

    start = end = None
    if day is not None:
        day_bucket = _bucket(day, DAY) if day >= DAY else int(day)
        start = day_bucket * DAY
        end = start + DAY

    matched = [
        agg for agg in _aggregates.values()
        if agg["granularity"] == "hour"
        and _matches(agg, exact, start, end)
    ]
    matched.sort(key=_sort_key, reverse=True)
    return [_public_aggregate(agg) for agg in matched]


# ----------------------------------------------------------------------
# 回放
# ----------------------------------------------------------------------

def replay(filters=None):
    """按时间正序回放聚合结果保留下来的事件。

    - 小时粒度下的敏感聚合：回放全部原文（``retained="full"``）；
    - 其余聚合（含天粒度敏感聚合，原文只存在小时桶）：回放保留的样本
      （``retained="sample"``）；
    - 操作人变更作为 ``kind="operator_change"`` 条目按时间并入。

    返回条目列表，事件条目形如
    ``{"kind": "event", "event": {...原文...}, "aggregate": {...定位信息...},
       "retained": "full"|"sample"}``。
    """
    filters = dict(filters or {})
    granularity = filters.pop("granularity", "hour")
    start = filters.pop("start", None)
    end = filters.pop("end", None)

    entries = []
    hour_aggs = [
        agg for agg in _aggregates.values()
        if agg["granularity"] == granularity
        and _matches(agg, filters, start, end)
    ]
    for agg in sorted(hour_aggs, key=lambda a: (
            a["first_seen"] if a["first_seen"] is not None else 0,
            a["bucket"], a["table"] or "", a["user"] or "", a["op"] or "")):
        locator = {k: agg[k] for k in
                   ("table", "user", "op", "granularity", "bucket",
                    "window_start", "window_end")}
        if agg["sensitive"] and granularity == "hour":
            key = (agg["granularity"], agg["table"], agg["user"],
                   agg["op"], agg["bucket"])
            for evt in _sensitive_raw.get(key, []):
                entries.append((_event_time(evt), {
                    "kind": "event",
                    "event": copy.deepcopy(evt),
                    "aggregate": dict(locator),
                    "retained": "full",
                }))
        else:
            for evt in agg["samples"]:
                entries.append((_event_time(evt), {
                    "kind": "event",
                    "event": copy.deepcopy(evt),
                    "aggregate": dict(locator),
                    "retained": "sample",
                }))

    for ch in _operator_changes:
        if filters.get("table") is not None and ch["table"] != filters["table"]:
            continue
        if filters.get("op") is not None and ch["op"] != filters["op"]:
            continue
        if start is not None and ch["time"] < start:
            continue
        if end is not None and ch["time"] >= end:
            continue
        entries.append((ch["time"], {
            "kind": "operator_change",
            "change": copy.deepcopy(ch),
        }))

    entries.sort(key=lambda item: item[0])
    return [item[1] for item in entries]


def operator_changes(table=None, op=None):
    """查询操作人变更记录，按时间倒序。"""
    out = [
        ch for ch in _operator_changes
        if (table is None or ch["table"] == table)
        and (op is None or ch["op"] == op)
    ]
    out.sort(key=lambda ch: ch["time"], reverse=True)
    return copy.deepcopy(out)
