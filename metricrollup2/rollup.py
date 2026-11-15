"""指标上卷：按事件时间落窗，迟到触发重算，分位数用可合并草图。

口径：
- metric 带 "ts"（事件时间）时按 window_size 向下取整落窗；无 ts 时用 "window" 标签。
- 迟到：ts 小于该维度已见水位线，触发所在窗口整体重算（重放窗口内事件，幂等）。
- 计数/求和可加；分位数用 Sketch（加权点对 + 配对压缩），可合并、可序列化。
- query(dim, window)：dim="*" 表示上卷（合并该窗口全部维度），与下钻求和一致。
"""

import math

_DEFAULTS = {"window_size": 60.0, "retention_windows": 1440, "sketch_capacity": 128}
_config = dict(_DEFAULTS)

_windows = {}       # (dim, key) -> bucket；key 为 ("t", start) 或 ("l", label)
_watermarks = {}    # dim -> 已见最大事件时间
_dim_index = {}     # dim -> set(key)，维度基数变化时整体重建
_index_rebuilds = 0
_max_start = None   # 全局最大窗口起点（用于保留期清理）


class Sketch:
    """可合并、可序列化的分位数草图：加权点对，超容量时相邻配对压缩。"""

    def __init__(self, capacity=None):
        self.capacity = capacity or _config["sketch_capacity"]
        self.entries = []  # [[value, weight], ...]

    def add(self, value, weight=1.0):
        self.entries.append([float(value), float(weight)])
        if len(self.entries) > self.capacity:
            self._compress()

    def _compress(self):
        self.entries.sort(key=lambda e: e[0])
        merged = []
        it = iter(self.entries)
        for a in it:
            b = next(it, None)
            if b is None:
                merged.append(a)
            else:
                w = a[1] + b[1]
                merged.append([(a[0] * a[1] + b[0] * b[1]) / w, w])
        self.entries = merged

    def merge(self, other):
        """合并两个草图，返回新草图（权重守恒，误差有界）。"""
        s = Sketch(self.capacity)
        s.entries = [list(e) for e in self.entries] + [list(e) for e in other.entries]
        while len(s.entries) > s.capacity:
            s._compress()
        return s

    def quantile(self, q):
        """按加权中点位插值；未压缩时退化为精确中位数。"""
        if not self.entries:
            return 0.0
        points = []
        pos = 0.0
        for v, w in sorted(self.entries):
            pos += w
            points.append((pos - w / 2.0, v))
        total = pos
        t = q * total
        if t <= points[0][0]:
            return points[0][1]
        if t >= points[-1][0]:
            return points[-1][1]
        for (p0, v0), (p1, v1) in zip(points, points[1:]):
            if p0 <= t <= p1:
                return v0 if p1 == p0 else v0 + (v1 - v0) * (t - p0) / (p1 - p0)
        return points[-1][1]

    def to_dict(self):
        return {"capacity": self.capacity, "entries": [list(e) for e in self.entries]}

    @classmethod
    def from_dict(cls, d):
        s = cls(d.get("capacity"))
        s.entries = [[float(v), float(w)] for v, w in d.get("entries", [])]
        return s


def configure(window_size=None, retention_windows=None, sketch_capacity=None):
    """调整窗口大小、保留窗口数、草图容量；None 表示保持原值。"""
    if window_size is not None:
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        _config["window_size"] = float(window_size)
    if retention_windows is not None:
        if retention_windows < 1:
            raise ValueError("retention_windows must be >= 1")
        _config["retention_windows"] = int(retention_windows)
    if sketch_capacity is not None:
        if sketch_capacity < 2:
            raise ValueError("sketch_capacity must be >= 2")
        _config["sketch_capacity"] = int(sketch_capacity)


def reset():
    """清空全部窗口与索引，恢复默认配置（测试/演示用）。"""
    global _index_rebuilds, _max_start
    _windows.clear()
    _watermarks.clear()
    _dim_index.clear()
    _index_rebuilds = 0
    _max_start = None
    _config.update(_DEFAULTS)


def stats():
    """索引与窗口概览：维度基数、窗口数、索引重建次数。"""
    return {
        "dims": len(_dim_index),
        "windows": len(_windows),
        "index_rebuilds": _index_rebuilds,
    }


def _num(x):
    return int(x) if float(x).is_integer() else x


def _window_start(ts):
    size = _config["window_size"]
    return _num(math.floor(float(ts) / size) * size)


def _resolve_key(window):
    if isinstance(window, str):
        return ("l", window)
    return ("t", _window_start(window))


def _new_bucket(start):
    end = _num(start + _config["window_size"]) if start is not None else None
    return {
        "count": 0, "sum": 0.0, "sketch": Sketch(), "events": [],
        "seen_ids": set(), "recomputes": 0, "late_events": 0,
        "start": start, "end": end,
    }


def _rebuild_index():
    global _index_rebuilds
    _dim_index.clear()
    for dim, key in _windows:
        _dim_index.setdefault(dim, set()).add(key)
    _index_rebuilds += 1


def _recompute(bucket):
    """重放窗口内事件重建聚合；同一事件集结果相同，幂等。"""
    bucket["count"] = 0
    bucket["sum"] = 0.0
    bucket["sketch"] = Sketch()
    for e in bucket["events"]:
        bucket["count"] += 1
        bucket["sum"] += e["value"]
        bucket["sketch"].add(e["value"])
    bucket["recomputes"] += 1


def _expire():
    """按保留期清理过期时间窗；维度因此消失时重建索引。"""
    if _max_start is None:
        return
    horizon = _max_start - (_config["retention_windows"] - 1) * _config["window_size"]
    doomed = [k for k in _windows if k[1][0] == "t" and k[1][1] < horizon]
    if not doomed:
        return
    before = {d for d, _ in _windows}
    for k in doomed:
        del _windows[k]
    if {d for d, _ in _windows} != before:
        _rebuild_index()


def _add_event(dim, event):
    global _max_start
    ts = event.get("ts")
    label = event.get("window")
    if ts is not None:
        start = _window_start(ts)
        key = ("t", start)
    else:
        start = None
        key = ("l", label)

    bucket = _windows.get((dim, key))
    if bucket is None:
        if dim not in _dim_index:
            _rebuild_index()  # 维度基数变化，重建索引
        bucket = _new_bucket(start)
        _windows[(dim, key)] = bucket
        _dim_index.setdefault(dim, set()).add(key)

    event_id = event.get("id")
    if event_id is not None:
        if event_id in bucket["seen_ids"]:
            return {"dim": dim, "window": key[1], "count": bucket["count"],
                    "sum": bucket["sum"], "late": False, "duplicate": True,
                    "recomputes": bucket["recomputes"]}
        bucket["seen_ids"].add(event_id)

    value = float(event.get("value", 0))
    bucket["events"].append({"ts": ts, "value": value, "id": event_id})

    late = ts is not None and ts < _watermarks.get(dim, float("-inf"))
    if late:
        bucket["late_events"] += 1
        _recompute(bucket)  # 迟到触发所在窗口重算
    else:
        bucket["count"] += 1
        bucket["sum"] += value
        bucket["sketch"].add(value)

    if ts is not None:
        _watermarks[dim] = max(ts, _watermarks.get(dim, float("-inf")))
        if start is not None and (_max_start is None or start > _max_start):
            _max_start = start
        _expire()

    return {"dim": dim, "window": key[1], "count": bucket["count"],
            "sum": bucket["sum"], "late": late, "duplicate": False,
            "recomputes": bucket["recomputes"]}


def add(metric):
    """写入指标。单个 dict、dict 带 "events" 批次、或事件列表；批次按事件时间切窗。"""
    if isinstance(metric, (list, tuple)):
        events, dim_default = list(metric), None
    elif isinstance(metric.get("events"), (list, tuple)):
        events, dim_default = list(metric["events"]), metric.get("dim")
    else:
        return _add_event(metric.get("dim", ""), metric)

    results = []
    for e in events:
        e = dict(e)
        e.setdefault("dim", dim_default or "")
        results.append(_add_event(e["dim"], e))
    return {"added": len(results), "late": sum(1 for r in results if r["late"]),
            "results": results}


def recompute(dim, window):
    """手动重算指定窗口；重放同一事件集，结果幂等。"""
    bucket = _windows.get((dim, _resolve_key(window)))
    if bucket is not None:
        _recompute(bucket)
    return query(dim, window)


def query(dim, window):
    """查询窗口聚合：窗口边界、计数/求和、p50、重算次数与草图。dim="*" 为上卷。"""
    key = _resolve_key(window)
    if dim == "*":
        buckets = [b for (d, k), b in _windows.items() if k == key]
    else:
        b = _windows.get((dim, key))
        buckets = [b] if b else []

    count = sum(b["count"] for b in buckets)
    total = sum(b["sum"] for b in buckets)
    recomputes = sum(b["recomputes"] for b in buckets)
    late_events = sum(b["late_events"] for b in buckets)
    sketch = Sketch()
    for b in buckets:
        sketch = sketch.merge(b["sketch"])

    if buckets:
        start, end = buckets[0]["start"], buckets[0]["end"]
    elif key[0] == "t":
        start, end = key[1], _num(key[1] + _config["window_size"])
    else:
        start = end = None

    return {
        "dim": dim, "window": window,
        "window_start": start, "window_end": end,
        "count": count, "sum": total, "p50": sketch.quantile(0.5),
        "recomputes": recomputes, "late_events": late_events,
        "sketch": sketch.to_dict(),
    }
