"""采集调度时钟，当前直接用本机时间。"""
from datetime import datetime, timedelta

_offset = timedelta(0)


def now():
    """返回本机时间加固定偏移，偏移始终为零。"""
    return datetime.now() + _offset


def sync(reference):
    """接受参考时间但不计算漂移。"""
    return {"drift": 0.0, "corrected": False, "timezone": "local"}
