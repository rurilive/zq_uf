"""可合并的分位数草图（t-digest 风格）。

小样本时保留原始值，结果精确；样本量超过 ``exact_limit`` 后压缩成加权质心。
质心按权重合并，因此既能按批增量更新，也能回放历史批次。新值先进入缓冲区，
攒够一批再压缩，保证每条数据的摊还开销是常数，内存不随行数增长。
"""

from __future__ import annotations

import math


class TDigest:
    def __init__(self, compression: int = 100, exact_limit: int = 200):
        self.compression = int(compression)
        self.exact_limit = int(exact_limit)
        self._raw = []          # 精确模式下的原始值；质心模式下为 None
        self._centroids = []    # [[mean, weight], ...]，按 mean 升序
        self._buffer = []       # [(value, weight), ...] 待压缩的新点
        self._flush_size = max(64, self.compression * 2)
        self.count = 0.0
        self.min = None
        self.max = None
        self._sum = 0.0

    # ------------------------------------------------------------------ 更新
    def add(self, value: float, weight: float = 1.0) -> None:
        value = float(value)
        weight = float(weight)
        if not math.isfinite(value) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("value must be finite and weight must be positive")

        self._touch(value, weight)
        if self._raw is not None:
            if weight == 1.0:
                self._raw.append(value)
                if self.count > self.exact_limit:
                    self._leave_exact()
                return
            self._leave_exact()

        self._buffer.append((value, weight))
        if len(self._buffer) >= self._flush_size:
            self._flush()

    def add_many(self, values) -> None:
        for value in values:
            self.add(value)

    def merge(self, other: "TDigest") -> "TDigest":
        """按权重合并另一个草图，用于跨批增量更新。"""
        if other is None or other.count <= 0:
            return self
        if self.count <= 0:
            self._adopt(other)
            return self

        if (self._raw is not None and other._raw is not None
                and not self._buffer and not other._buffer
                and self.count + other.count <= self.exact_limit):
            self._raw.extend(other._raw)
        else:
            self._leave_exact()
            if other._raw is not None:
                self._buffer.extend((v, 1.0) for v in other._raw)
            self._buffer.extend(other._buffer)
            self._buffer.extend((m, w) for m, w in other._centroids)

        self.count += other.count
        self._sum += other._sum
        self._update_bounds(other.min, other.max)
        self._flush()
        return self

    # ------------------------------------------------------------------ 查询
    def quantile(self, q: float):
        if self.count <= 0:
            return None
        q = min(max(float(q), 0.0), 1.0)

        if self._raw is not None:
            values = sorted(self._raw)
            if len(values) == 1:
                return values[0]
            pos = q * (len(values) - 1)
            low = int(math.floor(pos))
            high = min(low + 1, len(values) - 1)
            frac = pos - low
            return values[low] * (1.0 - frac) + values[high] * frac

        if self._buffer:
            self._flush()
        if not self._centroids:
            return None
        if len(self._centroids) == 1:
            return self._centroids[0][0]

        # 质心代表一段连续秩：权重 w 覆盖秩 [c, c+w-1]，中心取 (w-1)/2 偏移，
        # 与全量扫描时 floor/ceil 线性插值的定义保持一致，中位与尾部分位才不会
        # 出现由定义差异带来的系统偏差。
        target = q * (self.count - 1.0)
        centers = []
        cumulative = 0.0
        for mean, weight in self._centroids:
            centers.append((cumulative + (weight - 1.0) / 2.0, mean))
            cumulative += weight
        # 权重小于 1 时上式可能倒退，保证插值区间单调
        for index in range(1, len(centers)):
            if centers[index][0] < centers[index - 1][0]:
                centers[index] = (centers[index - 1][0], centers[index][1])

        if target <= centers[0][0]:
            return self.min
        if target >= centers[-1][0]:
            return self.max
        for (x0, y0), (x1, y1) in zip(centers, centers[1:]):
            if target <= x1:
                if x1 == x0:
                    return y1
                return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
        return centers[-1][1]

    @property
    def mean(self):
        return self._sum / self.count if self.count else None

    # ---------------------------------------------------------------- 内部实现
    def _touch(self, value, weight):
        if self.count <= 0:
            self.min = value
            self.max = value
        else:
            self.min = min(self.min, value)
            self.max = max(self.max, value)
        self.count += weight
        self._sum += value * weight

    def _update_bounds(self, low, high):
        if low is None or high is None:
            return
        self.min = low if self.min is None else min(self.min, low)
        self.max = high if self.max is None else max(self.max, high)

    def _adopt(self, other):
        """空草图直接接管另一个草图的状态（拷贝，不共享引用）。"""
        self.count = other.count
        self._sum = other._sum
        self.min = other.min
        self.max = other.max
        if other._raw is not None and not other._buffer:
            self._raw = list(other._raw)
            self._centroids = []
            self._buffer = []
        else:
            self._raw = None
            self._centroids = [[m, w] for m, w in other._centroids]
            self._buffer = [(v, w) for v, w in other._buffer]

    def _leave_exact(self):
        """离开精确模式：把原始值转入待压缩缓冲区。"""
        if self._raw is None:
            return
        raw = self._raw
        self._raw = None
        self._buffer.extend((v, 1.0) for v in raw)

    def _flush(self):
        if not self._buffer:
            return
        merged = self._centroids + [list(point) for point in self._buffer]
        self._buffer = []
        merged.sort(key=lambda p: p[0])
        self._centroids = self._compress(merged)

    def _compress(self, centroids):
        if not centroids:
            return []
        total = sum(w for _, w in centroids)
        if total <= 0:
            return []
        merged = []
        cumulative = 0.0
        for mean, weight in centroids:
            q = (cumulative + weight / 2.0) / total
            # 分位越靠两端，允许的质心重量越小，保证尾部精度
            limit = max(1.0, 4.0 * total * q * (1.0 - q) / self.compression)
            if merged and merged[-1][1] + weight <= limit:
                m0, w0 = merged[-1]
                merged[-1] = [(m0 * w0 + mean * weight) / (w0 + weight), w0 + weight]
            else:
                merged.append([mean, weight])
            cumulative += weight
        return merged

    # ---------------------------------------------------------------- 序列化
    def to_dict(self):
        if self._buffer:
            self._flush()
        return {
            "compression": self.compression,
            "exact_limit": self.exact_limit,
            "count": self.count,
            "min": self.min,
            "max": self.max,
            "sum": self._sum,
            "raw": list(self._raw) if self._raw is not None else None,
            "centroids": [[m, w] for m, w in self._centroids],
        }

    @classmethod
    def from_dict(cls, data) -> "TDigest":
        digest = cls(data.get("compression", 100), data.get("exact_limit", 200))
        digest.count = float(data.get("count", 0.0))
        digest.min = data.get("min")
        digest.max = data.get("max")
        digest._sum = float(data.get("sum", 0.0))
        raw = data.get("raw")
        if raw is not None:
            digest._raw = [float(v) for v in raw]
            digest._centroids = []
        else:
            digest._raw = None
            digest._centroids = [[float(m), float(w)]
                                 for m, w in data.get("centroids", [])]
        digest._buffer = []
        return digest
