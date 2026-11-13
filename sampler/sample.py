"""可复现的分层采样与统计摘要，纯标准库。"""
import math
import random

_DEFAULT_N = 10
_QUANTILE_LEVELS = ((0.5, "p50"), (0.9, "p90"), (0.99, "p99"))
_Z_95 = 1.96
# 历史遗留键，兼容旧调用，解析时忽略。
_IGNORED_KEYS = {"seed"}


class SampleResult(list):
    """list 子类，采样结果附带可序列化状态，便于重放。"""

    def __init__(self, rows, plan, seed, strata_counts):
        super().__init__(rows)
        self.plan = plan
        self.seed = seed
        self.strata_counts = strata_counts

    def to_state(self):
        """返回可 JSON 序列化的状态；相同输入下可据此重放。"""
        return {
            "version": 1,
            "plan": self.plan,
            "seed": self.seed,
            "strata_counts": self.strata_counts,
            "size": len(self),
        }


class Summary(dict):
    """dict 子类，键固定为 count/mean；分位数与误差挂在属性上。"""

    def __init__(self, count, mean, quantiles, error):
        super().__init__(count=count, mean=mean)
        self.quantiles = quantiles
        self.error = error

    def to_dict(self):
        return {
            "count": self["count"],
            "mean": self["mean"],
            "quantiles": self.quantiles,
            "error": self.error,
        }


def _parse_plan(plan):
    if not isinstance(plan, dict):
        raise TypeError("plan 必须是 dict")
    strata = plan.get("strata")
    if strata is not None and not isinstance(strata, str):
        raise TypeError("strata 必须是字段名字符串")
    n = plan.get("n")
    ratio = plan.get("ratio")
    if n is not None and ratio is not None:
        raise ValueError("n 和 ratio 只能二选一")
    if n is not None:
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError("n 必须是正整数")
        if n <= 0:
            raise ValueError("n 必须是正整数")
    if ratio is not None:
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise ValueError("ratio 必须在 (0, 1] 区间")
        if not 0 < ratio <= 1:
            raise ValueError("ratio 必须在 (0, 1] 区间")
    return strata, n, ratio, plan.get("random_seed")


def _allocate(sizes, total):
    """最大余数法把 total 分配到各层；层数不超过 total 时每层至少 1 条。"""
    keys = sorted(sizes, key=repr)
    population = sum(sizes.values())
    quotas = {key: total * sizes[key] / population for key in keys}
    alloc = {key: int(quotas[key]) for key in keys}
    if total >= len(keys):
        for key in keys:
            if alloc[key] < 1:
                alloc[key] = 1
    diff = total - sum(alloc.values())
    by_remainder = sorted(
        keys, key=lambda key: (quotas[key] - int(quotas[key]), repr(key)), reverse=True
    )
    while diff > 0:
        for key in by_remainder:
            if diff == 0:
                break
            if alloc[key] < sizes[key]:
                alloc[key] += 1
                diff -= 1
    while diff < 0:
        for key in reversed(by_remainder):
            if diff == 0:
                break
            if alloc[key] > 1:
                alloc[key] -= 1
                diff += 1
    return alloc


def sample(rows, plan):
    """分层采样。plan 支持 strata/n/ratio/random_seed，返回 SampleResult。"""
    rows = list(rows)
    strata, n, ratio, seed = _parse_plan(plan)
    rng = random.Random(seed) if seed is not None else random
    used_plan = {"strata": strata, "n": n, "ratio": ratio, "random_seed": seed}
    used_plan = {key: value for key, value in used_plan.items() if value is not None}
    if not rows:
        return SampleResult([], used_plan, seed, {})

    if strata is None:
        if ratio is not None:
            size = max(1, int(len(rows) * ratio + 0.5))
        else:
            size = n if n is not None else _DEFAULT_N
        picked = list(rows) if size >= len(rows) else rng.sample(rows, size)
        return SampleResult(picked, used_plan, seed, {})

    groups = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError("第 %d 行不是 dict，无法按字段分层" % index)
        if strata not in row:
            raise ValueError("第 %d 行缺少分层字段 %r" % (index, strata))
        groups.setdefault(row[strata], []).append(row)
    keys = sorted(groups, key=repr)
    sizes = {key: len(groups[key]) for key in keys}

    if ratio is not None:
        alloc = {key: max(1, min(sizes[key], int(sizes[key] * ratio + 0.5))) for key in keys}
    else:
        total = n if n is not None else _DEFAULT_N
        if total >= len(rows):
            counts = {key: sizes[key] for key in keys}
            return SampleResult(list(rows), used_plan, seed, counts)
        alloc = _allocate(sizes, total)

    picked = []
    for key in keys:
        take = alloc[key]
        part = list(groups[key]) if take >= sizes[key] else rng.sample(groups[key], take)
        picked.extend(part)
    return SampleResult(picked, used_plan, seed, alloc)


def replay(rows, state):
    """按 to_state() 产出的状态重放采样。"""
    if not isinstance(state, dict) or "plan" not in state:
        raise ValueError("state 必须包含 plan")
    return sample(rows, state["plan"])


def _quantile(sorted_values, p):
    count = len(sorted_values)
    if count == 1:
        return sorted_values[0]
    pos = p * (count - 1)
    low = int(pos)
    high = min(low + 1, count - 1)
    frac = pos - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def summary(rows):
    """统计摘要：count/mean 为字典键，分位数与误差在属性上。"""
    values = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "value" not in row:
            raise ValueError("第 %d 行缺少数值字段 'value'" % index)
        value = row["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("第 %d 行 'value' 不是数值" % index)
        values.append(value)
    count = len(values)
    if count == 0:
        return Summary(0, None, None, None)
    mean = sum(values) / count
    ordered = sorted(values)
    quantiles = {name: _quantile(ordered, p) for p, name in _QUANTILE_LEVELS}
    if count < 2:
        error = None
    else:
        variance = sum((value - mean) ** 2 for value in values) / (count - 1)
        error = _Z_95 * math.sqrt(variance / count)
    return Summary(count, mean, quantiles, error)
