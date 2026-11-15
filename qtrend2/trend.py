"""模块级门面：保留旧 check(dataset, {"value": x}) 接口，委托给默认引擎。"""
from __future__ import annotations

from .engine import Config, Engine

_default_engine = Engine()


def get_engine():
    return _default_engine


def reset(config: Config | None = None):
    """重置默认引擎（测试或新任务前调用）。"""
    global _default_engine
    _default_engine = Engine(config or Config())
    return _default_engine


def check(dataset, metric="value", value=None, period=None, shadow=False):
    """摄入一个点并返回告警结果；metric 支持旧的 dict 形式。"""
    return _default_engine.check(dataset, metric, value, period=period, shadow=shadow)


def trend(dataset, metric="value", window=None):
    """按 dataset × metric 返回趋势方向与拟合斜率。"""
    return _default_engine.trend(dataset, metric, window=window)


def mark_false_positive(dataset, metric="value", alert_id=None, note=None):
    """标记误报：同序列后续重复告警被抑制，恢复后自动重新武装。"""
    return _default_engine.mark_false_positive(dataset, metric, alert_id, note)


def label_shadow(alert_id, label):
    """给影子运行中的观测点打人工标签（1 真实问题 / 0 误报）。"""
    return _default_engine.label_shadow(alert_id, label)


def shadow_report():
    """影子运行对比报告：新基线 vs 旧固定阈值的误报率与召回。"""
    return _default_engine.shadow_report()


def backtest(points):
    """回测：同一份数据跑新基线与旧固定阈值。"""
    return _default_engine.backtest(points)


def sweep_thresholds(points, **kwargs):
    """阈值网格回测。"""
    return _default_engine.sweep_thresholds(points, **kwargs)
