"""事件时间滚动窗口。

口径：
- 按事件时间（event["ts"]）落窗，window_id = floor(ts / window_size)。
- 窗口以闭区间 [start, end] 描述，end = start + window_size；时间戳恰好落在
  边界上时，归入以该点为起点的右侧窗口（即 floor 规则），相邻窗口不会重复收数。
- 水位线 watermark = 已观察最大事件时间 - allowed_lateness；窗口在水位线严格
  越过其末端（end < watermark）后关闭，之后到达的事件判为迟到，迟到事件触发
  该窗口“撤销旧值 -> 全量重算 -> 写新值”。用严格小于而非 <=，闭区间 [start,
  end] 在末端点本身仍开放，边界归并规则不会与迟到判定互相矛盾。
- 重算永远从窗口内原始事件重新派生聚合值，重复调用不会累加，结果幂等；
  每次重算保留含旧值/新值的审计记录，窗口元数据与审计记录均落盘便于对账。
"""

import json
import math
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.RLock()

DEFAULT_WINDOW_SIZE = 86400          # 秒，默认一天，兼容历史自然日口径
DEFAULT_ALLOWED_LATENESS = 0
DEFAULT_STATE_DIR = os.environ.get("BATCHWINDOW_STATE_DIR", "batchwindow_state")

# 兼容历史用法：window_id -> 原始事件列表，仍是普通 dict，支持 .clear()
_buckets = {}

# 其余内存状态
_aggregates = {}        # window_id -> 当前聚合值 {"count", "sum"}
_late_log = []          # 迟到记录
_recompute_log = []     # 重算审计记录（含旧值/新值）
_event_ids = set()      # 已处理事件 id（事件带 "id" 时去重）
_max_ts = None          # 已观察到的最大事件时间
_next_record_id = 1

STATE_DIR = DEFAULT_STATE_DIR
WINDOW_SIZE = DEFAULT_WINDOW_SIZE
ALLOWED_LATENESS = DEFAULT_ALLOWED_LATENESS

_FILES = {
    "meta": "meta.json",
    "events": "events.json",
    "windows": "windows.json",
    "late": "late_events.json",
    "recompute": "recompute_records.json",
}


# ---------------------------------------------------------------- 基础工具

def _now():
    return datetime.now(timezone.utc).isoformat()


def _to_ts(ts):
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise TypeError("event['ts'] 必须是数值型时间戳")
    return ts


def window_id(ts):
    """事件时间落窗：floor(ts / window_size)，边界点归右侧窗口。"""
    return math.floor(_to_ts(ts) / WINDOW_SIZE)


def window_bounds(wid):
    """窗口闭区间端点 (start, end)，边界归属规则见模块文档。"""
    start = wid * WINDOW_SIZE
    return start, start + WINDOW_SIZE


def watermark():
    """当前水位线；尚无事件时为 None。"""
    if _max_ts is None:
        return None
    return _max_ts - ALLOWED_LATENESS


def _is_late(wid):
    wm = watermark()
    if wm is None:
        return False
    _, end = window_bounds(wid)
    return end < wm  # 水位线严格越过闭区间末端才关窗


def _zero_aggregate():
    return {"count": 0, "sum": 0}


def _aggregate_events(events):
    """从原始事件全量派生聚合值，这是幂等的基础：不依赖任何增量状态。"""
    agg = _zero_aggregate()
    for event in events:
        agg["count"] += 1
        value = event.get("value", 0)
        if value is not None:
            agg["sum"] += value
    return agg


# ---------------------------------------------------------------- 落盘

def _path(name):
    return os.path.join(STATE_DIR, _FILES[name])


def _read_json(name, default):
    if not STATE_DIR:
        return default
    path = _path(name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(name, data):
    if not STATE_DIR:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)  # 原子替换，避免半截文件


def _window_meta(wid):
    start, end = window_bounds(wid)
    events = _buckets.get(wid, [])
    agg = _aggregates.get(wid) or _aggregate_events(events)
    timestamps = [e["ts"] for e in events]
    return {
        "window": wid,
        "start": start,
        "end": end,
        "bounds": "[]",  # 闭区间；边界点归起始于该点的右侧窗口
        "boundary_rule": "timestamp equal to a boundary belongs to the window "
                         "starting at that boundary",
        "count": agg["count"],
        "sum": agg["sum"],
        "first_event_ts": min(timestamps) if timestamps else None,
        "last_event_ts": max(timestamps) if timestamps else None,
        "updated_at": _now(),
    }


def _persist():
    _atomic_write("events", {str(k): v for k, v in _buckets.items()})
    window_ids = sorted(set(_buckets) | set(_aggregates))
    _atomic_write("windows", {str(k): _window_meta(k) for k in window_ids})
    _atomic_write("late", _late_log)
    _atomic_write("recompute", _recompute_log)
    _atomic_write("meta", {
        "window_size": WINDOW_SIZE,
        "allowed_lateness": ALLOWED_LATENESS,
        "max_ts": _max_ts,
        "watermark": watermark(),
        "event_ids": sorted(_event_ids),
        "next_record_id": _next_record_id,
    })


def _load(apply_params=False):
    """从 STATE_DIR 恢复状态（configure 时调用）。

    apply_params 为 True 时才用落盘 meta 覆盖窗口参数；显式 configure 传入的
    参数优先，避免同目录残留 meta 把新参数静默改回旧值。
    """
    _buckets.clear()
    _aggregates.clear()
    _late_log.clear()
    _recompute_log.clear()
    _event_ids.clear()

    for raw_id, events in _read_json("events", {}).items():
        wid = int(raw_id)
        _buckets[wid] = events
        _aggregates[wid] = _aggregate_events(events)
    _late_log.extend(_read_json("late", []))
    _recompute_log.extend(_read_json("recompute", []))

    global _max_ts, _next_record_id
    meta = _read_json("meta", {})
    _max_ts = meta.get("max_ts")
    _next_record_id = meta.get("next_record_id", 1)
    _event_ids.update(meta.get("event_ids", []))
    if apply_params:
        if "window_size" in meta:
            globals()["WINDOW_SIZE"] = meta["window_size"]
        if "allowed_lateness" in meta:
            globals()["ALLOWED_LATENESS"] = meta["allowed_lateness"]


# ---------------------------------------------------------------- 撤销 / 重写

def _retract(wid):
    """撤销窗口旧聚合值，返回旧值（无旧值时为 None）。"""
    return _aggregates.pop(wid, None)


def _write_aggregate(wid, agg):
    """写入窗口新聚合值。"""
    _aggregates[wid] = agg
    return agg


def recompute(window, reason="manual", trigger_event=None):
    """重算指定窗口：先撤销旧值，再按全量原始事件重算并写新值。

    幂等：聚合值只从原始事件派生，重复调用结果一致，不会重复累加。
    每次调用都追加一条含 old_value/new_value 的审计记录并落盘。
    """
    global _next_record_id
    with _LOCK:
        wid = int(window)
        old_value = _retract(wid)
        new_value = _write_aggregate(wid, _aggregate_events(_buckets.get(wid, [])))
        record = {
            "id": _next_record_id,
            "window": wid,
            "reason": reason,                      # manual | late
            "old_value": old_value,                # 保留旧值，便于对账
            "new_value": dict(new_value),          # 重算后的新值
            "trigger_event": trigger_event,        # 迟到重算时的触发事件
            "watermark": watermark(),
            "created_at": _now(),
        }
        _next_record_id += 1
        _recompute_log.append(record)
        _persist()
        return {
            "window": wid,
            "count": new_value["count"],
            "sum": new_value["sum"],
            "reason": reason,
            "old_value": old_value,
            "new_value": dict(new_value),
        }


# ---------------------------------------------------------------- 落窗

def assign(event):
    """按事件时间落窗；迟到事件自动触发受影响窗口重算。

    返回值保留历史键 window/count，并补充 late/sum/watermark 等信息。
    事件带 "id" 时按 id 去重，重复投递不会二次入窗。
    """
    global _max_ts
    with _LOCK:
        if not isinstance(event, dict) or "ts" not in event:
            raise ValueError("event 必须是包含 'ts' 字段的 dict")
        ts = _to_ts(event["ts"])
        wid = window_id(ts)

        event_id = event.get("id")
        if event_id is not None and event_id in _event_ids:
            agg = _aggregates.get(wid) or _aggregate_events(_buckets.get(wid, []))
            return {
                "window": wid, "count": agg["count"], "sum": agg["sum"],
                "late": False, "duplicate": True, "watermark": watermark(),
            }

        late = _is_late(wid)
        previous_max_ts = _max_ts
        events = _buckets.setdefault(wid, [])
        events.append(event)
        if event_id is not None:
            _event_ids.add(event_id)
        if _max_ts is None or ts > _max_ts:
            _max_ts = ts

        if late:
            # 迟到：记录迟到事实，然后撤销旧值 -> 全量重算 -> 写新值
            _late_log.append({
                "event": event,
                "window": wid,
                "watermark": watermark(),
                "arrived_after_max_ts": previous_max_ts,
                "created_at": _now(),
            })
            result = recompute(wid, reason="late", trigger_event=event)
            result.update({"late": True, "duplicate": False,
                           "watermark": watermark()})
            return result

        # 非迟到同样走“撤销旧值 -> 全量派生 -> 写新值”，但不记重算审计
        _retract(wid)
        new_value = _write_aggregate(wid, _aggregate_events(events))
        _persist()
        return {
            "window": wid,
            "count": new_value["count"],
            "sum": new_value["sum"],
            "late": False,
            "duplicate": False,
            "watermark": watermark(),
        }


# ---------------------------------------------------------------- 查询 / 配置

def windows():
    """全部窗口元数据（闭区间边界、当前聚合值），按窗口 id 排序。"""
    with _LOCK:
        return [_window_meta(wid) for wid in sorted(set(_buckets) | set(_aggregates))]


def get_window(window):
    """查询单个窗口元数据，不存在返回 None。"""
    with _LOCK:
        wid = int(window)
        if wid not in _buckets and wid not in _aggregates:
            return None
        return _window_meta(wid)


def late_events(window=None):
    """迟到记录查询，可按窗口过滤。"""
    with _LOCK:
        return [dict(r) for r in _late_log if window is None or r["window"] == int(window)]


def recompute_records(window=None, start_window=None, end_window=None):
    """重算记录查询：可按单个窗口，或 [start_window, end_window] 区间过滤。"""
    with _LOCK:
        out = []
        for record in _recompute_log:
            wid = record["window"]
            if window is not None and wid != int(window):
                continue
            if start_window is not None and wid < int(start_window):
                continue
            if end_window is not None and wid > int(end_window):
                continue
            out.append(dict(record))
        return out


def configure(state_dir=None, window_size=None, allowed_lateness=None):
    """切换落盘目录与窗口参数；若目录中已有状态文件则恢复内存状态。

    显式传入的窗口参数优先；未传入的参数沿用当前值（不会被落盘 meta 覆盖）。
    """
    with _LOCK:
        _persist()  # 先把当前状态刷到旧目录
        if state_dir is not None:
            globals()["STATE_DIR"] = state_dir
        if window_size is not None:
            globals()["WINDOW_SIZE"] = window_size
        if allowed_lateness is not None:
            globals()["ALLOWED_LATENESS"] = allowed_lateness
        _load(apply_params=False)
        _persist()  # 以当前参数刷新新目录 meta


def reset(state_dir=None):
    """清空内存状态并删除落盘文件，同时恢复默认窗口参数，供测试 / 作业重跑使用。"""
    global _max_ts, _next_record_id, STATE_DIR, WINDOW_SIZE, ALLOWED_LATENESS
    with _LOCK:
        target = STATE_DIR if state_dir is None else state_dir
        if target and os.path.isdir(target):
            for fname in _FILES.values():
                path = os.path.join(target, fname)
                if os.path.exists(path):
                    os.remove(path)
        if state_dir is not None:
            STATE_DIR = state_dir
        _buckets.clear()
        _aggregates.clear()
        _late_log.clear()
        _recompute_log.clear()
        _event_ids.clear()
        _max_ts = None
        _next_record_id = 1
        WINDOW_SIZE = DEFAULT_WINDOW_SIZE
        ALLOWED_LATENESS = DEFAULT_ALLOWED_LATENESS
