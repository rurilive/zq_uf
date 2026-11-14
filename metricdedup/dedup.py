"""带窗口的幂等去重：按指标键 + 事件时间窗去重，状态有上限并可落盘恢复。"""

import json
import os

# 去重窗口（秒，事件时间）：ts <= 水位 - 窗口 的数据视为窗口外
_window_seconds = 60
# 状态条目上限，超出按最旧优先淘汰
_max_size = 10000
# 状态落盘路径，None 表示关闭持久化
_state_path = "metricdedup_state.json"

# 指标键 -> 该键已写入的最新事件时间戳
_seen = {}
_written = 0
_dup = 0
_expired = 0

_UNSET = object()


def configure(window_seconds=_UNSET, max_size=_UNSET, state_path=_UNSET):
    """调整窗口、上限和落盘路径；设置新路径后立即尝试加载。"""
    global _window_seconds, _max_size, _state_path
    if window_seconds is not _UNSET:
        _window_seconds = window_seconds
    if max_size is not _UNSET:
        _max_size = max_size
    if state_path is not _UNSET:
        _state_path = state_path
        if state_path:
            load_state()


def reset():
    """清空状态和计数（演示/测试用）。"""
    global _written, _dup, _expired
    _seen.clear()
    _written = 0
    _dup = 0
    _expired = 0


def _watermark():
    """事件时间水位：状态中的最新时间戳；状态为空则无窗口约束。"""
    return max(_seen.values()) if _seen else None


def _cleanup():
    """窗口外状态清理 + 上限淘汰，最旧优先，数量计入 _expired。"""
    global _expired
    wm = _watermark()
    if wm is not None:
        cutoff = wm - _window_seconds
        for key in sorted((k for k, ts in _seen.items() if ts <= cutoff),
                         key=lambda k: (_seen[k], k)):
            del _seen[key]
            _expired += 1
    while len(_seen) > _max_size:
        oldest = min(_seen.items(), key=lambda kv: (kv[1], kv[0]))[0]
        del _seen[oldest]
        _expired += 1


def put(metric):
    """写入一条指标；同键重复（ts 不更新）只计数，乱序旧值不覆盖新值。"""
    global _written, _dup, _expired
    name = metric["name"]
    ts = metric["ts"]
    last = _seen.get(name)
    if last is not None and ts <= last:
        _dup += 1
        _save()
        return {"written": False, "duplicate": True, "expired": False}
    wm = _watermark()
    if wm is not None and ts <= wm - _window_seconds:
        # 窗口外迟到数据：不写入，计为过期
        _expired += 1
        _save()
        return {"written": False, "duplicate": False, "expired": True}
    _seen[name] = ts
    _written += 1
    _cleanup()
    _save()
    return {"written": True, "duplicate": False, "expired": False}


def flush():
    """输出写入、重复、过期和状态大小。"""
    return {"written": _written, "duplicate": _dup,
            "expired": _expired, "size": len(_seen)}


def _save():
    """原子落盘：先写临时文件再替换。"""
    if not _state_path:
        return
    data = {"state": _seen, "written": _written,
            "duplicate": _dup, "expired": _expired}
    tmp = _state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _state_path)


def load_state():
    """从落盘文件恢复状态（重启恢复）；文件不存在或损坏则忽略。"""
    global _written, _dup, _expired
    if not _state_path or not os.path.exists(_state_path):
        return
    try:
        with open(_state_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    _seen.clear()
    _seen.update(data.get("state", {}))
    _written = data.get("written", 0)
    _dup = data.get("duplicate", 0)
    _expired = data.get("expired", 0)


load_state()  # 模块加载时恢复上次状态
