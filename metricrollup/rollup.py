"""可重算的指标上卷。

按事件时间把指标落到自然小时窗口（整数时间戳下窗口为闭区间
``[start, start + 3599]``）。原始事件会保留下来，迟到数据触发受影响
窗口整体重算，因此任意到达顺序下结果都一致（重算幂等）。

维度支持层级路径：``dim`` 可以是字符串或元组，上卷时父维度合并全部
子维度的窗口，计数/求和可加，分位数通过 t-digest 草图合并而非平均。
"""

import math

from .sketch import TDigest

WINDOW_SECONDS = 3600

# key=(维度路径元组, 窗口起点) -> 该窗口保留的原始事件列表
_events = {}
# key=(维度路径元组, 窗口起点) -> 聚合结果字典
_buckets = {}
# 已观察到的最大事件时间，作为水位线
_watermark = None
# 因迟到数据触发的窗口重算次数
_late_recomputations = 0


def reset():
    """清空全部状态，主要给测试使用。"""
    _events.clear()
    _buckets.clear()
    global _watermark, _late_recomputations
    _watermark = None
    _late_recomputations = 0


def _normalize_dim(dim):
    if isinstance(dim, str):
        if not dim:
            raise TypeError("dim 必须是非空字符串，或由非空字符串组成的元组")
        return (dim,)
    if isinstance(dim, tuple) and dim and all(
        isinstance(part, str) and part for part in dim
    ):
        return dim
    raise TypeError("dim 必须是非空字符串，或由非空字符串组成的元组")


def _window_start(timestamp):
    """按事件时间向下取整到自然小时窗口起点。"""
    return math.floor(timestamp / WINDOW_SECONDS) * WINDOW_SECONDS


def _validate_metric(metric):
    if not isinstance(metric, dict):
        raise TypeError("metric 必须是字典")
    for field in ("dim", "ts", "value"):
        if field not in metric:
            raise KeyError(f"metric 缺少必填字段: {field}")
    dim = _normalize_dim(metric["dim"])
    timestamp = metric["ts"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        raise TypeError("ts 必须是有限的 int 或 float（秒级时间戳）")
    value = metric["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError("value 必须是有限的 int 或 float")
    return dim, float(timestamp), float(value)


def _recompute(key):
    """依据保留的原始事件重建单个窗口，天然幂等。"""
    raw = _events[key]
    digest = TDigest()
    for event in raw:
        digest.add(event["value"])
    _buckets[key] = {
        "count": len(raw),
        "sum": sum(event["value"] for event in raw),
        "sketch": digest,
        "recomputations": _buckets.get(key, {}).get("recomputations", 0),
    }


def add(metric):
    """写入一条指标，按事件时间落窗；迟到数据触发受影响窗口重算。

    返回 ``(dim_path, window_start)``。
    """
    global _watermark, _late_recomputations
    dim, timestamp, value = _validate_metric(metric)
    start = _window_start(timestamp)
    key = (dim, start)

    is_late = _watermark is not None and timestamp < _watermark
    if key not in _events:
        _events[key] = []
    _events[key].append({"ts": timestamp, "value": value})

    if is_late:
        _late_recomputations += 1
        _recompute(key)
        _buckets[key]["recomputations"] += 1
    else:
        bucket = _buckets.get(key)
        if bucket is None:
            _recompute(key)
        else:
            bucket["count"] += 1
            bucket["sum"] += value
            bucket["sketch"].add(value)

    if _watermark is None or timestamp > _watermark:
        _watermark = timestamp
    return key


def recompute_all():
    """强制重算全部窗口，返回重算的窗口数量。"""
    for key in _events:
        _recompute(key)
    return len(_events)


def _normalize_window(window):
    if window is None:
        return None
    if isinstance(window, bool) or not isinstance(window, (int, float)):
        raise TypeError("window 必须是 int/float 窗口起点或 (start, end) 元组")
    start = _window_start(window)
    if start != window:
        raise ValueError(f"window 起点必须对齐自然小时（{WINDOW_SECONDS} 的整数倍）: {window}")
    return start


def _window_view(dim, start, bucket):
    digest = bucket["sketch"]
    return {
        "window_start": start,
        "window_end": start + WINDOW_SECONDS - 1,
        "count": bucket["count"],
        "sum": bucket["sum"],
        "p50": digest.quantile(0.50),
        "p95": digest.quantile(0.95),
        "p99": digest.quantile(0.99),
        "recomputations": bucket["recomputations"],
        "sketch": digest.state(),
        "dim": list(dim),
    }


def query(dim, window=None):
    """查询维度上卷结果。

    - ``dim`` 为字符串或层级路径元组；父路径合并其全部子维度。
    - ``window`` 省略时返回所有窗口；传窗口起点（小时对齐的 int/float）
      只返回该窗口；传 ``(start, end)`` 闭区间返回覆盖范围内的窗口。
    """
    dim_path = _normalize_dim(dim)
    range_start = range_end = None
    if window is not None and isinstance(window, tuple):
        if len(window) != 2:
            raise ValueError("window 区间必须是 (start, end)")
        range_start = _normalize_window(window[0])
        range_end = _normalize_window(window[1])
        if range_end < range_start:
            raise ValueError("window 区间 end 不能小于 start")
    elif window is not None:
        single = _normalize_window(window)
        range_start = range_end = single

    matched = sorted(
        ((key, bucket) for key, bucket in _buckets.items()
         if key[0][:len(dim_path)] == dim_path
         and (range_start is None or range_start <= key[1] <= range_end)),
        key=lambda item: (item[0][1], item[0][0]),
    )
    if not matched:
        known = sorted({key[0] for key in _buckets})
        raise KeyError(f"维度 {list(dim_path)} 在指定窗口内没有数据，已有维度: {[list(d) for d in known]}")

    windows = [_window_view(key[0], key[1], bucket) for key, bucket in matched]
    merged_sketch = TDigest.merge([bucket["sketch"] for _, bucket in matched])
    return {
        "dim": list(dim_path),
        "count": sum(bucket["count"] for _, bucket in matched),
        "sum": sum(bucket["sum"] for _, bucket in matched),
        "buckets": len(matched),
        "windows": windows,
        "late_recomputations": _late_recomputations,
        "watermark": _watermark,
        "sketch": merged_sketch.state(),
    }
