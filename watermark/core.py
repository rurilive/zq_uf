"""可配置的水位与窗口：分位水位、按数据源延迟、空闲推进、侧输出。"""

import bisect
import math


class Window:
    def __init__(self, size=10, quantile=1.0, allowed_lateness=0, idle_timeout=None):
        if not 0 < quantile <= 1:
            raise ValueError("quantile must be in (0, 1]")
        self.size = size
        self.quantile = quantile
        self.allowed_lateness = allowed_lateness
        self.idle_timeout = idle_timeout
        self.watermark = None
        self.buffer = []
        self.closed = []
        self.side_output = []
        self._seen = []
        self._pushes_since_advance = 0
        self._last_advance_ts = None

    def _lateness_for(self, event):
        if isinstance(self.allowed_lateness, dict):
            return self.allowed_lateness.get(
                event.get("source"), self.allowed_lateness.get("default", 0))
        return self.allowed_lateness

    def _window_of(self, ts):
        start = (ts // self.size) * self.size
        return (start, start + self.size)

    def _quantile(self):
        n = len(self._seen)
        idx = min(n - 1, max(0, math.ceil(self.quantile * n) - 1))
        return self._seen[idx]

    def _raise_watermark(self, candidate):
        if candidate is not None and (
                self.watermark is None or candidate > self.watermark):
            self.watermark = candidate

    def _window_closed(self, event):
        if self.watermark is None:
            return False
        _, end = self._window_of(event["ts"])
        return self.watermark > end + self._lateness_for(event)

    def push(self, event):
        bisect.insort(self._seen, event["ts"])
        self._pushes_since_advance += 1
        self._raise_watermark(self._quantile())
        if self._window_closed(event):
            start, end = self._window_of(event["ts"])
            self.side_output.append({
                "event": event,
                "window": (start, end),
                "lateness": self.watermark - end,
            })
            return
        self.buffer.append(event)

    def advance(self, ts):
        candidate = self._quantile() if self._seen else None
        idle = (self._pushes_since_advance == 0
                and self._last_advance_ts is not None
                and ts - self._last_advance_ts >= (self.idle_timeout or 0))
        if self.idle_timeout is None or idle:
            if candidate is None or ts > candidate:
                candidate = ts
        self._raise_watermark(candidate)
        self._pushes_since_advance = 0
        self._last_advance_ts = ts
        keep = []
        newly_closed = []
        for event in self.buffer:
            if self._window_closed(event):
                newly_closed.append(event)
            else:
                keep.append(event)
        self.buffer = keep
        self.closed.extend(newly_closed)
        return newly_closed
