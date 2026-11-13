"""数据质量基线漂移检测。

基于历史窗口和同周期基线计算均值与波动，偏离超过倍数才告警；
支持同比、环比、连续窗口和趋势斜率判断；纯标准库实现。
"""

from statistics import mean, pstdev

THRESHOLD = 0.1  # 旧固定阈值口径，仅用于无历史数据的兼容路径

DEFAULT_CONFIG = {
    "window": 7,        # 连续窗口：最近 N 天作为环比基线
    "period": 7,        # 周期长度，7 表示按周对齐
    "period_window": 4,  # 同比：取最近几个同周期样本
    "multiplier": 3.0,  # 偏离倍数：|z| 超过该倍数才告警
    "min_ratio": 0.05,  # 波动为 0 时按相对偏离折算 z 的最小刻度
    "trend_slope": 0.02,  # 缓慢漂移：每天相对均值的斜率上限
}


def _legacy_check(dataset, metric):
    """无历史数据时保持旧的固定阈值行为。"""
    value = metric.get("value")
    baseline = metric.get("baseline", 0)
    if value is None or baseline in (None, 0):
        return {"dataset": dataset, "alert": False, "reason": "no baseline"}
    drift = abs(value - baseline) / abs(baseline)
    return {"dataset": dataset, "alert": drift > THRESHOLD, "drift": round(drift, 4)}


def _ratio(value, base):
    return (value - base) / base if base else 0.0


def _zscore(value, base_mean, base_std, config):
    if base_std > 0:
        return (value - base_mean) / base_std
    scale = abs(base_mean) * config["min_ratio"]
    return (value - base_mean) / scale if scale else 0.0


def _slope(values):
    """最小二乘斜率，按均值归一化，表示每天的相对变化率。"""
    n = len(values)
    if n < 2:
        return 0.0
    mid_x = (n - 1) / 2
    mean_y = mean(values)
    denom = sum((x - mid_x) ** 2 for x in range(n))
    slope = sum((x - mid_x) * (y - mean_y) for x, y in enumerate(values)) / denom
    return slope / mean_y if mean_y else 0.0


def _same_period_samples(history, config):
    """从 history 末尾按周期对齐取同比样本（最近的在前面）。"""
    period = config["period"]
    samples = []
    idx = len(history) - period
    while idx >= 0 and len(samples) < config["period_window"]:
        samples.append(history[idx])
        idx -= period
    return samples


def check(dataset, metric):
    """检测单个指标是否漂移。

    metric 字段：
      value       当前值（必填）
      history     最近若干天的历史值，按时间升序（提供时走基线检测）
      same_period 显式同周期样本，缺省时按 config.period 从 history 对齐
      config      覆盖 DEFAULT_CONFIG 的阈值配置
      baseline    旧口径固定基线（无 history 时使用）
    """
    value = metric.get("value")
    history = metric.get("history")
    if value is None or not history:
        return _legacy_check(dataset, metric)

    config = dict(DEFAULT_CONFIG)
    config.update(metric.get("config", {}))
    history = list(history)

    recent = history[-config["window"]:]
    base_mean = mean(recent)
    base_std = pstdev(recent) if len(recent) > 1 else 0.0
    zscore = _zscore(value, base_mean, base_std, config)

    previous = history[-1]
    wow = _ratio(value, previous)  # 环比：对前一天

    period_samples = metric.get("same_period") or _same_period_samples(history, config)
    period_mean = mean(period_samples) if period_samples else None
    yoy = _ratio(value, period_mean) if period_mean else 0.0  # 同比：对同周期均值

    # 趋势用同周期对齐样本（含当前值），对季节模式免疫，斜率折算成每天
    trend_samples = list(reversed(period_samples)) + [value]
    slope = _slope(trend_samples) / config["period"] if len(trend_samples) >= 2 else 0.0

    reasons = []
    if abs(zscore) > config["multiplier"]:
        reasons.append("window drift")
    if len(period_samples) >= 2:
        period_std = pstdev(period_samples)
        period_z = _zscore(value, period_mean, period_std, config)
        if abs(period_z) > config["multiplier"]:
            reasons.append("period drift")
    if len(trend_samples) >= 3 and abs(slope) > config["trend_slope"]:
        reasons.append("trend drift")

    return {
        "dataset": dataset,
        "alert": bool(reasons),
        "value": value,
        "baseline": round(base_mean, 4),
        "deviation": round(_ratio(value, base_mean), 4),
        "zscore": round(zscore, 4),
        "window": len(recent),
        "wow": round(wow, 4),
        "yoy": round(yoy, 4),
        "trend_slope": round(slope, 4),
        "reasons": reasons,
    }


def merge_alerts(alerts):
    """合并同一数据集、同一原因的连续重复告警，附加 count 和起止日期。"""
    merged = []
    for alert in alerts:
        if (merged and merged[-1]["dataset"] == alert["dataset"]
                and merged[-1]["reasons"] == alert["reasons"]):
            merged[-1]["count"] += 1
            if "date" in alert:
                merged[-1]["last_date"] = alert["date"]
            continue
        entry = dict(alert)
        entry["count"] = 1
        if "date" in alert:
            entry["first_date"] = alert["date"]
            entry["last_date"] = alert["date"]
        merged.append(entry)
    return merged


def backtest(dataset, values, config=None):
    """用历史序列回测阈值：逐日回放，统计告警次数和合并后的告警段。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    start = cfg["window"] + 1
    values = list(values)
    alerts = []
    for i in range(start, len(values)):
        result = check(dataset, {"value": values[i], "history": values[:i], "config": cfg})
        if result["alert"]:
            result["date"] = i
            alerts.append(result)
    days = max(0, len(values) - start)
    return {
        "dataset": dataset,
        "days": days,
        "alerts": len(alerts),
        "alert_rate": round(len(alerts) / days, 4) if days else 0.0,
        "merged": merge_alerts(alerts),
    }
