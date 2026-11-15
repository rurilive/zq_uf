"""DDSketch：可合并的相对误差分位数草图。

每个值按 ``log_gamma`` 落到桶里，桶内用代表值 ``2*gamma**i/(gamma+1)`` 近似。
合并草图只需逐桶相加计数，因此上卷结果与合并顺序无关；查询任意分位数时
返回的代表值与真实值的相对误差不超过 ``alpha``。
"""

from __future__ import annotations

import math


class DDSketch:
    """可合并、带相对误差上界的分位数草图。"""

    def __init__(self, alpha: float = 0.01) -> None:
        alpha = float(alpha)
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        self.alpha = alpha
        self._gamma = (1.0 + alpha) / (1.0 - alpha)
        self._log_gamma = math.log(self._gamma)
        self._positive: dict[int, int] = {}
        self._negative: dict[int, int] = {}
        self._zeros = 0
        self._count = 0

    # ------------------------------------------------------------------ 写入
    def add(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        if value == 0.0:
            self._zeros += 1
        elif value > 0.0:
            index = math.ceil(math.log(value) / self._log_gamma)
            self._positive[index] = self._positive.get(index, 0) + 1
        else:
            index = math.ceil(math.log(-value) / self._log_gamma)
            self._negative[index] = self._negative.get(index, 0) + 1
        self._count += 1

    def merge(self, other: "DDSketch") -> "DDSketch":
        """把另一个草图并入当前草图（只加计数，可交换、可结合）。"""
        if not isinstance(other, DDSketch):
            raise TypeError("can only merge another DDSketch")
        if not math.isclose(other.alpha, self.alpha):
            raise ValueError("cannot merge sketches with different alpha")
        for index, count in other._positive.items():
            self._positive[index] = self._positive.get(index, 0) + count
        for index, count in other._negative.items():
            self._negative[index] = self._negative.get(index, 0) + count
        self._zeros += other._zeros
        self._count += other._count
        return self

    # ------------------------------------------------------------------ 查询
    @property
    def count(self) -> int:
        return self._count

    def _representative(self, index: int) -> float:
        return 2.0 * self._gamma ** index / (self._gamma + 1.0)

    def quantile(self, q: float) -> float:
        """返回分位数近似值；空草图返回 0.0。

        采用最近秩（nearest-rank）定义：秩为 ``ceil(q * count) - 1``，从 0 起。
        因此 ``p50`` 取中位那一侧的样本，``p100`` 取最大值，``p0`` 取最小值。
        """
        q = float(q)
        if not 0.0 <= q <= 1.0:
            raise ValueError("q must be in [0, 1]")
        if self._count == 0:
            return 0.0
        rank = max(0, math.ceil(q * self._count) - 1)
        seen = 0
        # 负值从小到大：桶下标越大越接近 0，先遍历大下标。
        for index in sorted(self._negative, reverse=True):
            seen += self._negative[index]
            if seen > rank:
                return -self._representative(index)
        seen += self._zeros
        if seen > rank:
            return 0.0
        for index in sorted(self._positive):
            seen += self._positive[index]
            if seen > rank:
                return self._representative(index)
        last = max(self._positive)
        return self._representative(last)

    # -------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        """导出可 JSON 化、可再次合并的分布快照。"""
        return {
            "alpha": self.alpha,
            "count": self._count,
            "zeros": self._zeros,
            "positive": {str(k): v for k, v in sorted(self._positive.items())},
            "negative": {str(k): v for k, v in sorted(self._negative.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DDSketch":
        sketch = cls(data.get("alpha", 0.01))
        sketch._zeros = int(data.get("zeros", 0))
        sketch._count = int(data.get("count", 0))
        sketch._positive = {int(k): int(v) for k, v in data.get("positive", {}).items()}
        sketch._negative = {int(k): int(v) for k, v in data.get("negative", {}).items()}
        return sketch

    def __len__(self) -> int:
        return self._count

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"DDSketch(alpha={self.alpha}, count={self._count})"
