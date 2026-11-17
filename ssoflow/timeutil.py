"""时间戳归一化。

事件可以带 epoch 数字（秒）或 ISO-8601 字符串，统一转成 epoch 秒（float）。
没有显式时间的事件由审计存储补单调的合成时间，保证链路耗时与失败间隔仍可计算。
"""

from datetime import datetime, timezone


def to_epoch(value):
    """把 epoch 数字 / 数字字符串 / ISO-8601 字符串转成 epoch 秒；None 原样返回。"""
    if value is None:
        return None
    # bool 是 int 的子类，时间戳不接受布尔值。
    if isinstance(value, bool):
        raise ValueError("invalid timestamp: %r" % (value,))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    raise ValueError("invalid timestamp: %r" % (value,))


def day_of(epoch):
    """epoch 秒对应的 UTC 日期，格式 YYYY-MM-DD，用于按天汇总。"""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
