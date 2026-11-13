"""按任务类型与数据量拟合耗时：近期样本加权、异常剔除、预测区间与回测。"""

import copy
import json

MIN_SAMPLES = 3    # 单个任务类型参与拟合的最少有效样本数，不足则退化为全局均值
DECAY = 0.9        # recency 衰减：样本每旧一条，权重乘 DECAY
OUTLIER_Z = 3.0    # 残差超过 OUTLIER_Z 倍稳健标准差（MAD）即标记为异常并剔除
Z_INTERVAL = 1.96  # 预测区间半宽系数（约 95%）

_DEFAULT_SAMPLES = [120, 180, 240, 300]

SAMPLES = []


def _check_task(task):
    if not isinstance(task, str) or not task:
        raise ValueError("task 必须是非空字符串")


def _check_size(size):
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        raise ValueError("size 必须是正数")


def _check_duration(duration):
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ValueError("duration 必须是非负数值")


def _record(task, size, duration):
    return {"task": task, "size": size, "duration": float(duration)}


def _normalize(samples):
    records = []
    for item in samples:
        if isinstance(item, dict):
            duration = item["duration"]
            _check_duration(duration)
            size = item.get("size")
            if size is not None:
                _check_size(size)
            records.append(_record(item.get("task"), size, duration))
        else:
            _check_duration(item)
            records.append(_record(None, None, item))
    return records


def reset(samples=None):
    """重置样本；元素可以是耗时数值（无类型）或 {task, size, duration} 记录。"""
    SAMPLES[:] = _normalize(samples if samples is not None else _DEFAULT_SAMPLES)


def add_sample(task, size, duration):
    """追加一条带任务类型与数据量的历史样本。"""
    _check_task(task)
    _check_size(size)
    _check_duration(duration)
    SAMPLES.append(_record(task, size, duration))


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _weights(n):
    return [DECAY ** (n - 1 - i) for i in range(n)]


def _fit(samples):
    """加权最小二乘拟合 duration = a + b * size，返回拟合量与残差。"""
    xs = [s["size"] for s in samples]
    ys = [s["duration"] for s in samples]
    ws = _weights(len(samples))
    total = sum(ws)
    xbar = sum(w * x for w, x in zip(ws, xs)) / total
    ybar = sum(w * y for w, y in zip(ws, ys)) / total
    sxx = sum(w * (x - xbar) ** 2 for w, x in zip(ws, xs))
    if sxx > 0:
        slope = sum(w * (x - xbar) * (y - ybar) for w, x, y in zip(ws, xs, ys)) / sxx
    else:
        slope = 0.0
    intercept = ybar - slope * xbar
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    return intercept, slope, xbar, sxx, total, ws, resid


def _robust_line(samples):
    """Theil-Sen 稳健拟合：斜率取两点斜率中位数，截距取 y - b*x 中位数。"""
    xs = [s["size"] for s in samples]
    ys = [s["duration"] for s in samples]
    slopes = []
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            dx = xs[j] - xs[i]
            if dx:
                slopes.append((ys[j] - ys[i]) / dx)
    slope = _median(slopes) if slopes else 0.0
    intercept = _median([y - slope * x for x, y in zip(xs, ys)])
    return intercept, slope


def _marked(samples):
    """按稳健拟合的残差标记异常样本，返回 (正常样本, 异常样本)。"""
    if len(samples) < MIN_SAMPLES + 1:
        return list(samples), []
    intercept, slope = _robust_line(samples)
    resid = [s["duration"] - (intercept + slope * s["size"]) for s in samples]
    center = _median(resid)
    deviations = [abs(r - center) for r in resid]
    sigma = 1.4826 * _median(deviations)
    if sigma <= 0:
        sigma = sum(deviations) / len(deviations)
    if sigma <= 0:
        return list(samples), []
    keep, out = [], []
    for sample, deviation in zip(samples, deviations):
        (out if deviation > OUTLIER_Z * sigma else keep).append(sample)
    if len(keep) < MIN_SAMPLES:
        return list(samples), []
    return keep, out


def _task_samples(task):
    return [s for s in SAMPLES if s["task"] == task]


def predict(task, size):
    """按任务类型与数据量预测耗时，返回 {eta, low, high, n, outliers}。"""
    _check_task(task)
    _check_size(size)
    samples = _task_samples(task)
    if len(samples) < MIN_SAMPLES:
        durations = [s["duration"] for s in SAMPLES]
        if not durations:
            raise ValueError("没有任何历史样本，无法预测")
        avg = sum(durations) / len(durations)
        return {"eta": avg, "low": avg, "high": avg, "n": len(durations), "outliers": 0}
    keep, out = _marked(samples)
    intercept, slope, xbar, sxx, total, ws, resid = _fit(keep)
    eta = intercept + slope * size
    df = total - 2
    s2 = sum(w * r * r for w, r in zip(ws, resid)) / df if df > 0 else 0.0
    leverage = 1.0 + 1.0 / total + ((size - xbar) ** 2 / sxx if sxx > 0 else 0.0)
    half = Z_INTERVAL * (s2 * leverage) ** 0.5
    return {
        "eta": eta,
        "low": eta - half,
        "high": eta + half,
        "n": len(keep),
        "outliers": len(out),
    }


def outliers(task=None):
    """返回被标记为异常的样本；不传 task 时检查所有任务类型。"""
    if task is not None:
        _check_task(task)
        tasks = [task]
    else:
        tasks = sorted({s["task"] for s in SAMPLES if s["task"] is not None})
    marked = []
    for name in tasks:
        _, out = _marked(_task_samples(name))
        marked.extend(out)
    return marked


def backtest(task):
    """走前向回测：用前 i 条样本拟合并预测第 i 条，汇总 MAE 与区间覆盖率。"""
    _check_task(task)
    samples = _task_samples(task)
    if not samples:
        raise ValueError("任务 %r 没有历史样本，无法回测" % task)
    if len(samples) < MIN_SAMPLES + 1:
        raise ValueError(
            "任务 %r 只有 %d 条样本，回测至少需要 %d 条"
            % (task, len(samples), MIN_SAMPLES + 1)
        )
    records = []
    for i in range(MIN_SAMPLES, len(samples)):
        keep, _ = _marked(samples[:i])
        intercept, slope, xbar, sxx, total, ws, resid = _fit(keep)
        actual = samples[i]
        eta = intercept + slope * actual["size"]
        df = total - 2
        s2 = sum(w * r * r for w, r in zip(ws, resid)) / df if df > 0 else 0.0
        leverage = 1.0 + 1.0 / total + (
            (actual["size"] - xbar) ** 2 / sxx if sxx > 0 else 0.0
        )
        half = Z_INTERVAL * (s2 * leverage) ** 0.5
        low, high = eta - half, eta + half
        records.append({
            "size": actual["size"],
            "actual": actual["duration"],
            "eta": eta,
            "low": low,
            "high": high,
            "hit": low <= actual["duration"] <= high,
        })
    mae = sum(abs(r["actual"] - r["eta"]) for r in records) / len(records)
    coverage = sum(1 for r in records if r["hit"]) / len(records)
    return {
        "task": task,
        "count": len(records),
        "mae": mae,
        "coverage": coverage,
        "records": records,
    }


def to_dict():
    """导出可 JSON 序列化的完整状态，便于重启后复现。"""
    return {
        "version": 1,
        "params": {
            "min_samples": MIN_SAMPLES,
            "decay": DECAY,
            "outlier_z": OUTLIER_Z,
            "z_interval": Z_INTERVAL,
        },
        "samples": copy.deepcopy(SAMPLES),
    }


def from_dict(state):
    """从 to_dict 导出的状态恢复样本。"""
    if not isinstance(state, dict) or "samples" not in state:
        raise ValueError("状态格式无效：缺少 samples 字段")
    reset(state["samples"])


def dumps():
    return json.dumps(to_dict())


def loads(text):
    try:
        state = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("状态 JSON 解析失败: %s" % exc) from exc
    from_dict(state)


reset()
