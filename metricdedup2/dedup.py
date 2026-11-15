"""指标去重，当前同键后到覆盖。"""

_state = {}


def put(metric):
    """同键直接覆盖，不比较版本。"""
    key = metric.get("key", "")
    _state[key] = dict(metric)
    return {"key": key, "version": metric.get("version"), "dropped": 0, "size": len(_state)}


def flush():
    """没有窗口水位，状态原样保留。"""
    return {"flushed": 0, "expired": 0, "size": len(_state)}
