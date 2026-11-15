"""按事件时间的指标上卷：分窗、迟到重算、分布合并、过期清理。

要点：

* 事件按 ``event_time`` 落到固定宽度的窗口（UTC 对齐），跨窗批次自动拆分。
* 计数与求和直接相加；分位数用可合并草图（DDSketch）先合并分布再取值，
  结果与合并顺序无关，相对误差不超过 ``alpha``。
* 水位线（已见最大事件时间）越过窗口结束时间后窗口关闭；之后到达的迟到
  事件只重算其所在窗口，并在返回中标记 ``recomputed``。
* 早于 ``watermark - ttl`` 的窗口自动清理；查询已清理的窗口返回
  ``expired=True`` 且计数为 0。
* 下钻（逐窗口）与上卷（区间）结果一致：区间结果是逐窗口结果合并而来。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .sketch import DDSketch

DEFAULT_WINDOW_SECONDS = 3600
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_ALPHA = 0.01
DEFAULT_QUANTILES = (0.5, 0.9, 0.99)


# --------------------------------------------------------------------- 时间
def _to_epoch(value) -> float:
    """把 int/float 秒、datetime 或 ISO 8601 字符串转成 epoch 秒。"""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"cannot parse time {value!r}") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    raise TypeError(f"unsupported time type: {type(value)!r}")


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quantile_label(q: float) -> str:
    pct = q * 100.0
    if abs(pct - round(pct)) < 1e-9:
        return f"p{int(round(pct))}"
    return f"p{pct:.6g}".rstrip("0").rstrip(".")


def _parse_quantile(value) -> float:
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("p"):
            text = text[1:]
        q = float(text)
        return q / 100.0 if q > 1.0 else q
    q = float(value)
    return q / 100.0 if q > 1.0 else q


# --------------------------------------------------------------------- 窗口
class _Window:
    """单个基础窗口的状态：原始值、可合并草图与聚合量。"""

    __slots__ = ("dim", "start", "end", "values", "sketch", "count", "sum",
                 "min", "max", "recomputed", "recomputes")

    def __init__(self, dim: str, start: int, size: int, alpha: float) -> None:
        self.dim = dim
        self.start = start
        self.end = start + size
        self.values: list[float] = []
        self.sketch = DDSketch(alpha)
        self.count = 0
        self.sum = 0.0
        self.min = None
        self.max = None
        self.recomputed = False
        self.recomputes = 0

    def append(self, value: float) -> None:
        self.values.append(value)
        self.sketch.add(value)
        self.count += 1
        self.sum += value
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)

    def rebuild(self) -> None:
        """迟到事件到达后，仅用本窗口原始值重算该窗口。"""
        sketch = DDSketch(self.sketch.alpha)
        total = 0.0
        for value in self.values:
            sketch.add(value)
            total += value
        self.sketch = sketch
        self.sum = total
        self.count = len(self.values)
        self.min = min(self.values) if self.values else None
        self.max = max(self.values) if self.values else None
        self.recomputed = True
        self.recomputes += 1


# --------------------------------------------------------------------- 存储
class RollStore:
    """按事件时间分窗的指标存储，支持上卷、下钻与过期清理。"""

    def __init__(self, window_seconds: int = DEFAULT_WINDOW_SECONDS,
                 ttl_seconds: int | None = DEFAULT_TTL_SECONDS,
                 alpha: float = DEFAULT_ALPHA) -> None:
        if int(window_seconds) != window_seconds or window_seconds <= 0:
            raise ValueError("window_seconds must be a positive integer")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive or None")
        self.window_seconds = int(window_seconds)
        self.ttl_seconds = ttl_seconds
        self.alpha = float(alpha)
        self._windows: dict[tuple[str, int], _Window] = {}
        self._watermark: float | None = None

    # ------------------------------------------------------------- 基础操作
    def reset(self) -> None:
        self._windows.clear()
        self._watermark = None

    @property
    def watermark(self) -> float | None:
        return self._watermark

    def _window_start(self, ts: float) -> int:
        return int(math.floor(ts / self.window_seconds)) * self.window_seconds

    def _is_expired(self, key: tuple[str, int]) -> bool:
        if self.ttl_seconds is None or self._watermark is None:
            return False
        start = key[1]
        return start + self.window_seconds <= self._watermark - self.ttl_seconds

    def purge_expired(self) -> int:
        """清掉早于水位线 TTL 的窗口，返回清理数量。"""
        if self.ttl_seconds is None or self._watermark is None:
            return 0
        cutoff = self._watermark - self.ttl_seconds
        stale = [key for key, win in self._windows.items() if win.end <= cutoff]
        for key in stale:
            del self._windows[key]
        return len(stale)

    # ------------------------------------------------------------- 写入路径
    @staticmethod
    def _normalise(metric: Mapping) -> tuple[str, float, list[float]]:
        if not isinstance(metric, Mapping):
            raise TypeError("metric must be a mapping")
        dim = metric.get("dim", "")
        raw_time = metric.get("event_time", metric.get("window"))
        if raw_time is None:
            raise ValueError("metric needs event_time")
        ts = _to_epoch(raw_time)
        raw_values = metric.get("values")
        if raw_values is None:
            raw_values = [metric.get("value", 0)]
        elif isinstance(raw_values, (int, float)):
            raw_values = [raw_values]
        values = []
        for raw in raw_values:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("value must be finite")
            values.append(value)
        if not values:
            raise ValueError("metric has no values")
        return dim, ts, values

    def add(self, metric: Mapping) -> dict:
        """写入单个事件，返回其所在窗口的概要。"""
        return self.add_batch([metric])[0]

    def add_batch(self, metrics: Iterable[Mapping]) -> list[dict]:
        """写入一批事件，按事件时间拆分到各自窗口。

        批次内部先按事件时间排序再处理，因此同一批里跨窗的事件不会互相
        误判为迟到；真正迟到（早于已有水位线）的事件才会触发窗口重算。
        """
        prepared = [self._normalise(metric) for metric in metrics]
        prepared.sort(key=lambda item: item[1])  # 稳定排序：同一时刻保持输入顺序

        self.purge_expired()

        touched: dict[tuple[str, int], _Window] = {}
        dropped: list[dict] = []
        for dim, ts, values in prepared:
            start = self._window_start(ts)
            key = (dim, start)
            if self._is_expired(key):
                # 窗口已过期并被清理：迟到太久，丢弃而不是重建。
                dropped.append({"dim": dim, "window": _fmt(start),
                                "window_start": _fmt(start),
                                "window_end": _fmt(start + self.window_seconds),
                                "count": 0, "sum": 0.0, "dropped": True,
                                "expired": True, "recomputed": False,
                                "reason": "expired_window"})
                continue

            window = self._windows.get(key)
            if window is None:
                window = _Window(dim, start, self.window_seconds, self.alpha)
                self._windows[key] = window

            late = self._watermark is not None and window.end <= self._watermark
            for value in values:
                window.append(value)
            if late:
                # 窗口已被水位线关闭，事件迟到：只重算这一个窗口。
                window.rebuild()

            if self._watermark is None or ts > self._watermark:
                self._watermark = ts
            touched[key] = window

        self.purge_expired()
        summaries = [self._window_summary(win) for win in
                     sorted(touched.values(), key=lambda w: (w.start, w.dim))]
        summaries.extend(dropped)
        return summaries

    # ------------------------------------------------------------- 结果拼装
    @staticmethod
    def _window_summary(win: _Window) -> dict:
        return {
            "dim": win.dim,
            "window": _fmt(win.start),
            "window_start": _fmt(win.start),
            "window_end": _fmt(win.end),
            "count": win.count,
            "sum": win.sum,
            "recomputed": win.recomputed,
            "recomputes": win.recomputes,
        }

    @staticmethod
    def _quantiles(sketch: DDSketch, quantiles: Sequence[float]) -> dict:
        return {_quantile_label(q): sketch.quantile(q) for q in quantiles}

    @staticmethod
    def _error_ranges(values: Mapping[str, float], alpha: float) -> dict:
        ranges = {}
        for label, value in values.items():
            if value == 0.0:
                low = high = 0.0
            else:
                low = min(value / (1.0 + alpha), value / (1.0 - alpha))
                high = max(value / (1.0 + alpha), value / (1.0 - alpha))
            ranges[label] = {"relative": alpha, "low": low, "high": high}
        return ranges

    def _window_entry(self, dim: str, start: int, quantiles: Sequence[float]) -> dict:
        win = self._windows.get((dim, start))
        if win is None:
            empty = DDSketch(self.alpha)
            qs = self._quantiles(empty, quantiles)
            return {
                "dim": dim,
                "window": _fmt(start),
                "window_start": _fmt(start),
                "window_end": _fmt(start + self.window_seconds),
                "count": 0,
                "sum": 0.0,
                "min": None,
                "max": None,
                "quantiles": qs,
                "error": self._error_ranges(qs, self.alpha),
                "distribution": empty.to_dict(),
                "recomputed": False,
                "expired": self._is_expired((dim, start)),
            }
        qs = self._quantiles(win.sketch, quantiles)
        return {
            "dim": dim,
            "window": _fmt(start),
            "window_start": _fmt(start),
            "window_end": _fmt(start + self.window_seconds),
            "count": win.count,
            "sum": win.sum,
            "min": win.min,
            "max": win.max,
            "quantiles": qs,
            "error": self._error_ranges(qs, self.alpha),
            "distribution": win.sketch.to_dict(),
            "recomputed": win.recomputed,
            "expired": False,
        }

    # ------------------------------------------------------------- 查询路径
    def _resolve_range(self, window, start, end) -> tuple[int, int]:
        if window is not None:
            if start is not None or end is not None:
                raise ValueError("pass either window or start/end, not both")
            if isinstance(window, (tuple, list)):
                start, end = window
            else:
                begin = self._window_start(_to_epoch(window))
                return begin, begin + self.window_seconds
        if start is None or end is None:
            raise ValueError("query needs a window or start and end")
        begin = _to_epoch(start)
        finish = _to_epoch(end)
        for edge in (begin, finish):
            if abs(edge / self.window_seconds - round(edge / self.window_seconds)) > 1e-9:
                raise ValueError("start/end must align to window boundaries")
        begin, finish = int(round(begin)), int(round(finish))
        if finish <= begin:
            raise ValueError("end must be after start")
        return begin, finish

    def query(self, dim: str, window=None, *, start=None, end=None,
              quantiles: Sequence = DEFAULT_QUANTILES) -> dict:
        """查询一个基础窗口或一个对齐的区间，返回上卷后的指标。"""
        self.purge_expired()
        begin, finish = self._resolve_range(window, start, end)
        parsed_qs = [_parse_quantile(q) for q in quantiles]

        merged = DDSketch(self.alpha)
        total = 0.0
        count = 0
        low = high = None
        recomputed = False
        expired = False
        entries = []

        for offset in range(begin, finish, self.window_seconds):
            entry = self._window_entry(dim, offset, parsed_qs)
            entries.append(entry)
            expired = expired or entry["expired"]
            win = self._windows.get((dim, offset))
            if win is not None:
                merged.merge(win.sketch)
            count += entry["count"]
            total += entry["sum"]
            recomputed = recomputed or entry["recomputed"]
            if entry["count"]:
                low = entry["min"] if low is None else min(low, entry["min"])
                high = entry["max"] if high is None else max(high, entry["max"])

        qs = self._quantiles(merged, parsed_qs)
        return {
            "dim": dim,
            "window": _fmt(begin),
            "window_start": _fmt(begin),
            "window_end": _fmt(finish),
            "count": count,
            "sum": total,
            "min": low,
            "max": high,
            "quantiles": qs,
            "error": self._error_ranges(qs, self.alpha),
            "alpha": self.alpha,
            "recomputed": recomputed,
            "expired": expired,
            "windows": entries,
        }

    def drilldown(self, dim: str, window=None, *, start=None, end=None,
                  quantiles: Sequence = DEFAULT_QUANTILES) -> list[dict]:
        """逐基础窗口下钻；这些结果合并后与同区间的上卷结果一致。"""
        return self.query(dim, window, start=start, end=end,
                          quantiles=quantiles)["windows"]


# ----------------------------------------------------------------- 默认实例
_default_store = RollStore()


def add(metric: Mapping) -> dict:
    return _default_store.add(metric)


def add_batch(metrics: Iterable[Mapping]) -> list[dict]:
    return _default_store.add_batch(metrics)


def query(dim: str, window=None, **kwargs) -> dict:
    return _default_store.query(dim, window, **kwargs)


def drilldown(dim: str, window=None, **kwargs) -> list[dict]:
    return _default_store.drilldown(dim, window, **kwargs)


def reset() -> None:
    _default_store.reset()
