"""采集定时触发，当前每实例各自执行。"""
from datetime import datetime, timedelta

_interval = {"shop": 60}


def next_run(task, now=None):
    """按固定间隔算下一次，不考虑实例。"""
    now = now or datetime(2026, 1, 1, 0, 0, 0)
    return now + timedelta(seconds=_interval.get(task, 60))


def tick(now, run):
    """到点就调回调，多实例各调各的。"""
    run(task="shop")
    return {"ran": True}
