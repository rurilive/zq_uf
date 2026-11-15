"""质量趋势与阈值模块：基线 + 偏离倍数告警，替代写死的单日阈值。

口径：
- 基线 = 最近窗口（window）+ 同周期窗口（seasonal_window，按星期对齐）的均值/标准差；
- 基线样本不足 min_samples 时回退固定阈值 fallback_threshold；
- 偏离倍数 = |value - 基线均值| / 基线标准差，达到 deviation_multiple 才告警；
- 偏离处于 [drift_multiple, deviation_multiple) 且连续 drift_windows 个窗口，确认缓慢漂移；
- 告警按 (dataset, metric) 去重：同一原因连续触发只报第一次。
"""

import json
import math
from datetime import date, datetime

DEFAULT_CONFIG = {
    "window": 7,               # 最近窗口样本数
    "seasonal_window": 4,      # 同周期（按星期对齐）样本数
    "min_samples": 3,          # 基线最少样本数，不足回退固定阈值
    "fallback_threshold": 0.05,  # 基线不足时的回退单日阈值
    "deviation_multiple": 3.0,   # 告警偏离倍数（相对基线标准差）
    "drift_multiple": 1.5,       # 缓慢漂移起步倍数
    "drift_windows": 3,          # 漂移确认所需连续窗口数
    "history_tail": 10,          # 告警附带历史曲线片段长度
}

_CONFIG = dict(DEFAULT_CONFIG)
_series = {}        # (dataset, metric) -> [record]
_drift_streak = {}  # (dataset, metric) -> 连续漂移窗口计数
_last_alert = {}    # (dataset, metric) -> 最近一次告警原因（去重用）


def configure(**overrides):
    """更新全局配置（基线窗口、偏离倍数等），返回生效配置。"""
    _check_keys(overrides)
    _CONFIG.update(overrides)
    return dict(_CONFIG)


def reset():
    """清空全部历史与配置，回到初始状态（主要供测试使用）。"""
    _CONFIG.update(DEFAULT_CONFIG)
    _series.clear()
    _drift_streak.clear()
    _last_alert.clear()


def _check_keys(mapping):
    unknown = set(mapping) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("未知配置项: %s" % ", ".join(sorted(unknown)))


def _weekday(day):
    """把 day 归一成星期几（0=周一）；无法识别时返回 None。"""
    if day is None:
        return None
    if isinstance(day, int) and 0 <= day <= 6:
        return day
    if isinstance(day, (date, datetime)):
        return day.weekday()
    try:
        return datetime.strptime(str(day)[:10], "%Y-%m-%d").weekday()
    except ValueError:
        return None


def _baseline(records, weekday, cfg):
    """最近窗口 + 同周期窗口合并算均值/标准差；样本不足返回 None。"""
    pool = [r["value"] for r in records[-cfg["window"]:]]
    if weekday is not None:
        same = [r["value"] for r in records if r["weekday"] == weekday]
        pool += same[-cfg["seasonal_window"]:]
    if len(pool) < cfg["min_samples"]:
        return None
    mean = sum(pool) / len(pool)
    var = sum((v - mean) ** 2 for v in pool) / len(pool)
    std = max(math.sqrt(var), abs(mean) * 0.01, 1e-9)
    return {"mean": mean, "std": std, "samples": len(pool)}


def _evaluate(value, records, weekday, cfg, streak):
    """对单个值判定，返回 (fired_reason, deviation, base, new_streak)。"""
    base = _baseline(records, weekday, cfg)
    if base is None:
        fired = "fallback_threshold" if value > cfg["fallback_threshold"] else None
        return fired, None, base, 0
    deviation = (value - base["mean"]) / base["std"]
    level = abs(deviation)
    if level >= cfg["deviation_multiple"]:
        return "deviation", deviation, base, 0
    if level >= cfg["drift_multiple"]:
        streak += 1
        fired = "drift" if streak >= cfg["drift_windows"] else None
        return fired, deviation, base, streak
    return None, deviation, base, 0


def check(dataset, metric, config=None):
    """检查一个指标值，按基线偏离倍数告警；告警带基线、偏离、样本和历史片段。"""
    cfg = dict(_CONFIG)
    if config:
        _check_keys(config)
        cfg.update(config)
    name = metric.get("name", "default")
    key = (dataset, name)
    value = float(metric.get("value", 0))
    weekday = _weekday(metric.get("day"))
    records = _series.setdefault(key, [])

    fired, deviation, base, streak = _evaluate(
        value, records, weekday, cfg, _drift_streak.get(key, 0))
    _drift_streak[key] = streak

    alert = bool(fired)
    deduped = False
    if alert and _last_alert.get(key) == fired:
        alert, deduped = False, True
    if alert:
        _last_alert[key] = fired
    elif fired is None:
        _last_alert.pop(key, None)

    records.append({
        "value": value,
        "weekday": weekday,
        "alert": alert,
        "reason": fired if (alert or deduped) else None,
        "false_positive": False,
    })

    return {
        "dataset": dataset,
        "metric": name,
        "value": value,
        "alert": alert,
        "deduped": deduped,
        "reason": fired if (alert or deduped) else None,
        "baseline": base["mean"] if base else None,
        "deviation": deviation,
        "samples": base["samples"] if base else 0,
        "history": [r["value"] for r in records[-cfg["history_tail"]:]],
    }


def trend(dataset, metric="default"):
    """返回历史值与方向：样本不足为 None，否则 up/down/flat。"""
    records = _series.get((dataset, metric), [])
    values = [r["value"] for r in records]
    direction = None
    if len(values) >= _CONFIG["min_samples"]:
        half = len(values) // 2
        early = values[:len(values) - half]
        recent = values[len(values) - half:]
        mean = sum(values) / len(values)
        std = max(math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)), 1e-9)
        diff = sum(recent) / len(recent) - sum(early) / len(early)
        if diff > 0.5 * std:
            direction = "up"
        elif diff < -0.5 * std:
            direction = "down"
        else:
            direction = "flat"
    return {"dataset": dataset, "metric": metric, "history": values, "direction": direction}


def feedback(dataset, metric="default", index=None, false_positive=True):
    """把一条告警标记为误报（或取消标记）；默认标记最近一次告警。"""
    records = _series.get((dataset, metric), [])
    alerts = [i for i, r in enumerate(records) if r["alert"]]
    if not alerts:
        return None
    i = index if index is not None else alerts[-1]
    if not 0 <= i < len(records):
        raise IndexError("样本下标越界: %r" % (i,))
    records[i]["false_positive"] = bool(false_positive)
    return records[i]


def backtest(dataset, metric="default", config=None):
    """用当前（或指定）配置回放历史样本，输出告警数、误报数和命中率。"""
    cfg = dict(_CONFIG)
    if config:
        _check_keys(config)
        cfg.update(config)
    records = _series.get((dataset, metric), [])
    alerts = 0
    hits = 0
    streak = 0
    last_reason = None
    replayed = []
    for r in records:
        fired, _, _, streak = _evaluate(r["value"], replayed, r["weekday"], cfg, streak)
        if fired is None:
            last_reason = None
        else:
            if fired != last_reason:
                alerts += 1
                if not r.get("false_positive"):
                    hits += 1
            last_reason = fired
        replayed.append(r)
    return {
        "dataset": dataset,
        "metric": metric,
        "samples": len(records),
        "alerts": alerts,
        "false_positives": alerts - hits,
        "hit_rate": (hits / alerts) if alerts else None,
    }


def export(dataset, metric="default", path=None):
    """导出趋势结果（趋势、序列、回测）；给了 path 就写成 JSON 文件。"""
    data = {
        "dataset": dataset,
        "metric": metric,
        "trend": trend(dataset, metric),
        "series": [dict(r) for r in _series.get((dataset, metric), [])],
        "backtest": backtest(dataset, metric),
    }
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data
