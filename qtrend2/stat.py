"""基线与趋势的纯统计函数：无状态、无第三方依赖。"""
from __future__ import annotations

import math

REL_FLOOR = 1e-9


def median(values):
    xs = sorted(values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def median_abs_deviation(values, med=None):
    if med is None:
        med = median(values)
    return median([abs(v - med) for v in values])


def robust_scale(values, med):
    """1.4826*MAD（正态一致的稳健标准差）。

    MAD 为 0 时保持 0，不回退到样本标准差——单个离群点会把标准差放大，
    随后真正的断崖反而测不出来（基线污染）。尺度为 0 时由调用方用
    相对幅度裁决，而不是用被污染的离散度。
    """
    return 1.4826 * median_abs_deviation(values, med)


def baseline(samples):
    """根据基线样本计算中位数、MAD 与稳健尺度。"""
    med = median(samples)
    return {
        "median": med,
        "mad": median_abs_deviation(samples, med),
        "scale": robust_scale(samples, med),
        "n": len(samples),
    }


def deviation(value, med, scale, direction):
    """当前点相对基线的方向化偏离量、相对幅度与稳健 z 分。

    direction: down 只看下降，up 只看上升，both 看绝对偏离。
    尺度为 0（基线完全平稳）时，任何不同取值 z 记为 inf，交由相对幅度过滤。
    """
    denom = max(abs(med), REL_FLOOR)
    if direction == "down":
        dev = med - value
    elif direction == "up":
        dev = value - med
    else:
        dev = abs(value - med)
    rel = dev / denom
    if scale == 0.0:
        z = math.inf if value != med else 0.0
    else:
        z = abs(value - med) / scale
    return {"deviation": dev, "relative_change": rel, "z": z}


def linear_trend(values):
    """最小二乘拟合 slope/intercept，x 为 0..n-1。"""
    n = len(values)
    if n < 2:
        return 0.0, (values[0] if values else 0.0)
    xbar = (n - 1) / 2.0
    ybar = sum(values) / n
    var = sum((x - xbar) ** 2 for x in range(n))
    cov = sum((x - xbar) * (y - ybar) for x, y in enumerate(values))
    slope = cov / var
    return slope, ybar - slope * xbar
