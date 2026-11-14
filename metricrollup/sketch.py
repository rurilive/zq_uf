"""可合并的分位数草图（t-digest，纯 Python 标准库）。

数据量未超过压缩阈值 ``delta`` 时每个观测值都是一个质心，
此时分位数查询是精确的；超过阈值后按 t-digest 的尺度函数压缩，
合并两个草图与把全部观测值喂进一个草图给出一致的估计。
"""

import math


class TDigest:
    """确定性的合并式分位数草图。"""

    def __init__(self, delta=100.0, centroids=None):
        self.delta = float(delta)
        # 质心列表：[均值, 权重]，始终按均值升序。
        self.centroids = [list(c) for c in centroids] if centroids else []

    @classmethod
    def from_values(cls, values, delta=100.0):
        digest = cls(delta=delta)
        for value in values:
            digest.add(value)
        return digest

    def add(self, value, weight=1.0):
        if not math.isfinite(value):
            raise ValueError("草图只接受有限数值")
        if weight <= 0:
            raise ValueError("权重必须为正数")
        insert_at = len(self.centroids)
        for index, centroid in enumerate(self.centroids):
            if value < centroid[0]:
                insert_at = index
                break
        self.centroids.insert(insert_at, [float(value), float(weight)])
        if self.size > self.delta:
            self._compress()

    @classmethod
    def merge(cls, digests, delta=100.0):
        """合并若干草图；输入不会被修改。"""
        combined = cls(delta=delta)
        for digest in digests:
            for mean, weight in digest.centroids:
                combined.add(mean, weight)
        return combined

    def _scale_limit(self, proportion):
        return (4.0 * self.delta * proportion * (1.0 - proportion)) if self.total_weight else 0.0

    def _compress(self):
        total = sum(weight for _, weight in self.centroids)
        if total == 0:
            return
        compressed = []
        cumulative = 0.0
        current_mean, current_weight = self.centroids[0]
        for mean, weight in self.centroids[1:]:
            proportion = cumulative / total
            if current_weight + weight <= self._scale_limit(proportion):
                merged_weight = current_weight + weight
                current_mean = (current_mean * current_weight + mean * weight) / merged_weight
                current_weight = merged_weight
            else:
                compressed.append([current_mean, current_weight])
                cumulative += current_weight
                current_mean, current_weight = mean, weight
        compressed.append([current_mean, current_weight])
        self.centroids = compressed

    @property
    def total_weight(self):
        return sum(weight for _, weight in self.centroids)

    @property
    def size(self):
        return len(self.centroids)

    def quantile(self, fraction):
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("分位点必须落在 [0, 1]")
        if not self.centroids:
            raise ValueError("空草图无法计算分位数")
        target = fraction * self.total_weight
        cumulative = 0.0
        for index, (mean, weight) in enumerate(self.centroids):
            half = weight / 2.0
            if target < cumulative + half:
                if index == 0:
                    return mean
                previous_mean = self.centroids[index - 1][0]
                left = cumulative
                right = cumulative + half
                if right == left:
                    return mean
                ratio = (target - left) / (right - left)
                return previous_mean + ratio * (mean - previous_mean)
            if target <= cumulative + weight:
                if index == len(self.centroids) - 1:
                    return mean
                next_mean = self.centroids[index + 1][0]
                left = cumulative + half
                right = cumulative + weight
                if right == left:
                    return mean
                ratio = (target - left) / (right - left)
                return mean + ratio * (next_mean - mean)
            cumulative += weight
        return self.centroids[-1][0]

    def state(self):
        """返回可序列化的草图状态。"""
        return {
            "algorithm": "t-digest",
            "delta": self.delta,
            "size": self.size,
            "total_weight": self.total_weight,
            "centroids": [[mean, weight] for mean, weight in self.centroids],
        }

    def equals(self, other, tolerance=1e-9):
        if self.size != other.size:
            return False
        for (mean_a, weight_a), (mean_b, weight_b) in zip(self.centroids, other.centroids):
            if abs(mean_a - mean_b) > tolerance or abs(weight_a - weight_b) > tolerance:
                return False
        return True

    def __eq__(self, other):
        return isinstance(other, TDigest) and self.equals(other)

    def __repr__(self):
        return f"TDigest(delta={self.delta}, centroids={self.centroids!r})"
