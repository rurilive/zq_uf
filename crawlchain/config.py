"""可配项：慢阶段阈值、保留期，环境变量可覆盖。"""

import os


def slow_threshold_ms(default=1000.0):
    """慢阶段阈值（毫秒），可用 CRAWLCHAIN_SLOW_MS 覆盖。"""
    return float(os.environ.get("CRAWLCHAIN_SLOW_MS", default))


def retention_hours(default=24.0):
    """链路数据保留期（小时），默认按任务保留最近一天，可用 CRAWLCHAIN_RETENTION_HOURS 覆盖。"""
    return float(os.environ.get("CRAWLCHAIN_RETENTION_HOURS", default))
