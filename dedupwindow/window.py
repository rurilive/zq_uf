"""带水位和滑动窗口的有界流式去重。

口径：
- seen(key, ts) 按事件时间判重，重复返回 True。
- advance(watermark) 推进水位，清理 ts < watermark - window_size 的状态。
- 迟到但 watermark - allowed_lateness <= ts < watermark 的数据仍可判重。
- 超出允许范围（ts < watermark - allowed_lateness）标记为过期，返回 False。
- 状态超过 max_size 时按最旧 ts 淘汰，并记录丢弃数。
"""

import json

_DEFAULT_WINDOW_SIZE = 10_000
_DEFAULT_ALLOWED_LATENESS = 10_000
_DEFAULT_MAX_SIZE = 10_000

_seen = {}  # key -> 事件时间 ts
_watermark = None
_window_size = _DEFAULT_WINDOW_SIZE
_allowed_lateness = _DEFAULT_ALLOWED_LATENESS
_max_size = _DEFAULT_MAX_SIZE
_hits = 0
_late = 0
_expired = 0
_dropped = 0


def reset():
    """清空状态与计数，并恢复默认配置。"""
    global _watermark, _window_size, _allowed_lateness, _max_size
    global _hits, _late, _expired, _dropped
    _seen.clear()
    _watermark = None
    _window_size = _DEFAULT_WINDOW_SIZE
    _allowed_lateness = _DEFAULT_ALLOWED_LATENESS
    _max_size = _DEFAULT_MAX_SIZE
    _hits = _late = _expired = _dropped = 0


def configure(window_size=None, allowed_lateness=None, max_size=None):
    """调整窗口、允许迟到范围和状态上限，未传的参数保持不变。"""
    global _window_size, _allowed_lateness, _max_size
    if window_size is not None:
        _window_size = window_size
    if allowed_lateness is not None:
        _allowed_lateness = allowed_lateness
    if max_size is not None:
        _max_size = max_size


def seen(key, ts):
    """按事件时间判重；过期返回 False，命中返回 True。"""
    global _hits, _late, _expired, _dropped
    if _watermark is not None and ts < _watermark - _allowed_lateness:
        _expired += 1
        return False
    if _watermark is not None and ts < _watermark:
        _late += 1
    if key in _seen:
        _hits += 1
        return True
    _seen[key] = ts
    while len(_seen) > _max_size:
        oldest = min(_seen, key=_seen.get)
        del _seen[oldest]
        _dropped += 1
    return False


def advance(watermark):
    """推进水位（只前进不后退），清理窗口外状态，返回剩余大小。"""
    global _watermark
    if _watermark is None or watermark > _watermark:
        _watermark = watermark
    cutoff = _watermark - _window_size
    for key in [k for k, t in _seen.items() if t < cutoff]:
        del _seen[key]
    return len(_seen)


def stats():
    """输出去重命中、迟到、过期、丢弃和状态大小。"""
    return {
        "hits": _hits,
        "late": _late,
        "expired": _expired,
        "dropped": _dropped,
        "size": len(_seen),
        "watermark": _watermark,
    }


def dumps():
    """序列化状态（水位、窗口配置、去重集合、计数）为 JSON 字符串。"""
    return json.dumps({
        "watermark": _watermark,
        "window_size": _window_size,
        "allowed_lateness": _allowed_lateness,
        "max_size": _max_size,
        "entries": [[k, t] for k, t in _seen.items()],
        "counters": {"hits": _hits, "late": _late,
                     "expired": _expired, "dropped": _dropped},
    })


def loads(data):
    """从 dumps() 的输出恢复水位、窗口配置和去重状态。"""
    global _watermark, _window_size, _allowed_lateness, _max_size
    global _hits, _late, _expired, _dropped
    state = json.loads(data)
    _watermark = state["watermark"]
    _window_size = state["window_size"]
    _allowed_lateness = state["allowed_lateness"]
    _max_size = state["max_size"]
    _seen.clear()
    _seen.update((k, t) for k, t in state["entries"])
    counters = state["counters"]
    _hits = counters["hits"]
    _late = counters["late"]
    _expired = counters["expired"]
    _dropped = counters["dropped"]
