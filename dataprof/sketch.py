"""可合并的分位数草图（纯标准库）。

原理：新值先进缓冲区；缓冲区满后与已压缩样本归并排序，再相邻两两压缩
（权重相加，交替保留上/下样本以抵消偏差）。两个草图合并 = 样本序列归并
后压缩，误差随压缩轮数有界，因此增量画像与全量画像的分位数误差可控。
"""


def _merge_sorted(left, right):
    out = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i][0] <= right[j][0]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out


def _compress(samples):
    out = []
    keep_upper = False
    for i in range(0, len(samples) - 1, 2):
        (v1, w1), (v2, w2) = samples[i], samples[i + 1]
        out.append((v2 if keep_upper else v1, w1 + w2))
        keep_upper = not keep_upper
    if len(samples) % 2:
        out.append(samples[-1])
    return out


class QuantileSketch:
    """容量有界的分位数草图，支持 add / merge / quantile / 序列化。"""

    def __init__(self, capacity=128):
        if capacity < 8:
            raise ValueError("capacity too small")
        self.capacity = int(capacity)
        self.count = 0
        self._buffer = []
        self._samples = []  # 按值排序的 (value, weight)

    def __len__(self):
        return self.count

    def add(self, value):
        self._buffer.append(float(value))
        self.count += 1
        if len(self._buffer) >= self.capacity:
            self._compact()

    def update(self, values):
        for value in values:
            self.add(value)

    def merge(self, other):
        """并入另一个草图，返回自身。"""
        samples = _merge_sorted(self._samples, other._samples)
        samples = _merge_sorted(samples, sorted((v, 1) for v in other._buffer))
        self._samples = samples
        self.count += other.count
        self._compact()
        return self

    def quantile(self, p):
        """返回 p 分位数的近似值；空草图返回 None。"""
        if not 0.0 <= p <= 1.0:
            raise ValueError("p must be in [0, 1]")
        self._compact()
        if not self._samples:
            return None
        total = sum(w for _, w in self._samples)
        target = p * (total - 1)
        cum = 0
        for value, weight in self._samples:
            cum += weight
            if cum > target:
                return value
        return self._samples[-1][0]

    def _compact(self):
        if self._buffer:
            self._samples = _merge_sorted(
                self._samples, sorted((v, 1) for v in self._buffer))
            self._buffer = []
        while len(self._samples) > self.capacity:
            self._samples = _compress(self._samples)

    def to_dict(self):
        self._compact()
        return {
            "capacity": self.capacity,
            "count": self.count,
            "samples": [list(s) for s in self._samples],
        }

    @classmethod
    def from_dict(cls, data):
        sketch = cls(data["capacity"])
        sketch.count = data["count"]
        sketch._samples = [tuple(s) for s in data["samples"]]
        return sketch
