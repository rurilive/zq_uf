"""指标合并：按事件时间落窗，迟到触发整窗重算，分位数用草图合并。

口径：
- 窗口边界按事件时间对齐（WINDOW_SIZE_SECONDS，UTC）；显式 window 字段优先。
- 水位线 = 见过的最大事件时间；event_time 早于水位线即迟到，触发整窗重算。
- 重算先按窗口主键撤销旧版本，再从窗口事件重放写入新版本（覆盖，不累加）。
- 超过保留期（RETENTION_SECONDS）的迟到数据进入补算队列，不落窗。
- 带 id 的事件按窗口去重，重复迟到不重复累加；计数/求和可加，分位数走草图。
"""

import math
from datetime import datetime, timezone

WINDOW_SIZE_SECONDS = 86400
RETENTION_SECONDS = 7 * 86400
SKETCH_CAPACITY = 128

_windows = {}   # 窗口主键 -> 窗口状态
_emitted = {}   # 窗口主键 -> 已输出版本（重算时撤销旧值再写）
_backfill = []  # 补算队列：超过保留期的迟到数据
_watermark = None
_seq = 0


class Sketch:
    """可合并分位数草图：加权样本 + 确定性压缩，状态可 JSON 序列化。"""

    def __init__(self, capacity=SKETCH_CAPACITY, items=None, compactions=0):
        self.capacity = capacity
        self.items = items if items is not None else []  # [[value, weight], ...]
        self.compactions = compactions

    def add(self, value):
        self.items.append([float(value), 1])
        self._maybe_compact()

    def merge(self, other):
        self.items.extend([list(item) for item in other.items])
        self._maybe_compact()
        return self

    @property
    def count(self):
        return sum(weight for _, weight in self.items)

    def quantile(self, p):
        if not self.items:
            return None
        p = min(max(p, 0.0), 1.0)
        points = sorted(self.items, key=lambda item: item[0])
        total = sum(weight for _, weight in points)
        target = max(p * total, 1e-9)
        cumulative = 0
        for value, weight in points:
            cumulative += weight
            if cumulative >= target:
                return value
        return points[-1][0]

    def _maybe_compact(self):
        while len(self.items) > self.capacity:
            self._compact()

    def _compact(self):
        # 排序后相邻配对，交替取左/右降低偏差，权重相加，保证总权重守恒。
        self.items.sort(key=lambda item: item[0])
        pairs = self.items
        leftover = None
        if len(pairs) % 2 == 1:
            leftover = pairs[-1]
            pairs = pairs[:-1]
        pick_left = self.compactions % 2 == 0
        compacted = []
        for i in range(0, len(pairs), 2):
            a, b = pairs[i], pairs[i + 1]
            chosen = a if pick_left else b
            compacted.append([chosen[0], a[1] + b[1]])
        if leftover is not None:
            compacted.append(leftover)
        self.items = compacted
        self.compactions += 1

    def to_dict(self):
        return {
            "capacity": self.capacity,
            "compactions": self.compactions,
            "items": [[value, weight] for value, weight in self.items],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            capacity=data.get("capacity", SKETCH_CAPACITY),
            items=[[float(v), int(w)] for v, w in data.get("items", [])],
            compactions=data.get("compactions", 0),
        )


def _parse_event_time(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise TypeError("event_time 必须是 epoch 秒或 ISO 8601 字符串")


def _align(ts):
    """窗口边界按事件时间对齐。"""
    start = math.floor(ts / WINDOW_SIZE_SECONDS) * WINDOW_SIZE_SECONDS
    return start, start + WINDOW_SIZE_SECONDS


def _window_key(start):
    return datetime.fromtimestamp(start, tz=timezone.utc).isoformat()


def _new_window(key, start=None, end=None):
    return {
        "key": key,
        "start": start,
        "end": end,
        "events": {},  # 事件 id -> (event_time, value)，重算的数据源
        "count": 0,
        "sum": 0.0,
        "sketch": Sketch(),
        "dirty": False,
        "version": 0,
    }


def _quantiles(sketch):
    return {
        "p50": sketch.quantile(0.5),
        "p90": sketch.quantile(0.9),
        "p99": sketch.quantile(0.99),
    }


def _recompute_window(key):
    """整窗重算：撤销旧版本，从事件重放，覆盖写入新版本。"""
    state = _windows[key]
    retracted = _emitted.pop(key, None)  # 撤销旧值

    count = 0
    total = 0.0
    sketch = Sketch()
    for _, value in state["events"].values():
        count += 1
        total += value
        sketch.add(value)

    state["count"] = count
    state["sum"] = total
    state["sketch"] = sketch
    state["version"] += 1
    state["dirty"] = False

    emitted = {
        "version": state["version"],
        "count": count,
        "sum": total,
        "quantiles": _quantiles(sketch),
        "sketch": sketch.to_dict(),
        # 合并前后基数对账：事件数、计数、草图权重必须一致
        "cardinality": {
            "events": len(state["events"]),
            "count": count,
            "sketch_weight": sketch.count,
            "ok": len(state["events"]) == count == sketch.count,
        },
    }
    _emitted[key] = emitted  # 重算结果覆盖旧版本
    return retracted, emitted


def add(metric):
    """按事件时间落窗累加；迟到数据触发整窗重算，超保留期进补算队列。"""
    global _watermark, _seq
    value = float(metric.get("value", 0))
    ts = _parse_event_time(metric.get("event_time"))

    start = end = None
    if ts is not None:
        start, end = _align(ts)
    if metric.get("window") is not None:
        key = str(metric["window"])
    elif start is not None:
        key = _window_key(start)
    else:
        key = ""

    late = ts is not None and _watermark is not None and ts < _watermark
    if ts is not None:
        _watermark = ts if _watermark is None else max(_watermark, ts)

    if end is not None and _watermark is not None and end <= _watermark - RETENTION_SECONDS:
        _backfill.append({
            "metric": dict(metric),
            "window": key,
            "reason": "retention_expired",
        })
        return {"window": key, "backfilled": True, "reason": "retention_expired"}

    state = _windows.get(key)
    if state is None:
        state = _windows[key] = _new_window(key, start, end)

    event_id = metric.get("id")
    if event_id is not None:
        event_id = str(event_id)
        if event_id in state["events"]:
            # 重复迟到：幂等，不重复累加
            return {
                "window": key,
                "count": state["count"],
                "sum": state["sum"],
                "deduplicated": True,
            }
    else:
        _seq += 1
        event_id = "_seq%d" % _seq

    state["events"][event_id] = (ts, value)
    state["count"] += 1
    state["sum"] += value
    state["sketch"].add(value)

    result = {"window": key, "count": state["count"], "sum": state["sum"], "late": late}
    if late:
        state["dirty"] = True
        retracted, emitted = _recompute_window(key)
        result.update({
            "recomputed": True,
            "retracted": retracted,
            "version": emitted["version"],
            "count": emitted["count"],
            "sum": emitted["sum"],
            "cardinality": emitted["cardinality"],
        })
    return result


def recompute(window):
    """重算指定窗口：脏窗口撤销旧值再写；干净窗口幂等返回 recomputed=False。"""
    key = str(window)
    state = _windows.get(key)
    if state is None:
        return {
            "window": key,
            "recomputed": False,
            "count": 0,
            "sum": 0.0,
            "version": 0,
            "sketch": None,
        }
    if not state["dirty"]:
        return {
            "window": key,
            "recomputed": False,
            "count": state["count"],
            "sum": state["sum"],
            "version": state["version"],
            "quantiles": _quantiles(state["sketch"]),
            "sketch": state["sketch"].to_dict(),
        }
    retracted, emitted = _recompute_window(key)
    return {
        "window": key,
        "recomputed": True,
        "retracted": retracted,
        **emitted,
    }


def merge_sketches(*sketch_dicts):
    """合并多个序列化草图，并对合并前后基数对账。"""
    merged = Sketch()
    weights = []
    for data in sketch_dicts:
        sketch = Sketch.from_dict(data)
        weights.append(sketch.count)
        merged.merge(sketch)
    return {
        "sketch": merged.to_dict(),
        "quantiles": _quantiles(merged),
        "cardinality": {
            "inputs": weights,
            "merged": merged.count,
            "ok": merged.count == sum(weights),
        },
    }


def backfill_queue():
    """超过保留期的迟到数据队列。"""
    return list(_backfill)


def windows():
    """输出全部窗口的当前状态（窗口、版本、草图）。"""
    return [
        {
            "window": key,
            "count": state["count"],
            "sum": state["sum"],
            "version": state["version"],
            "dirty": state["dirty"],
            "sketch": state["sketch"].to_dict(),
        }
        for key, state in sorted(_windows.items())
    ]


def reset():
    """清空全部状态（测试用）。"""
    global _watermark, _seq
    _windows.clear()
    _emitted.clear()
    del _backfill[:]
    _watermark = None
    _seq = 0
