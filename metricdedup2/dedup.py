"""指标去重：同键保留最高版本，按事件时间窗口清理，状态落盘可恢复。

版本口径
    同键比较键为 ``(event_time, version)``：event_time 大者视为更新；event_time
    相同再比较 version。没有 event_time 的记录排在带时间戳记录之后，彼此之间
    只按 version 比较。迟到旧版本只计数（``dropped``），不覆盖已有结果。

窗口口径
    watermark 是已见最大 event_time，只增不减。窗口保留
    ``event_time >= watermark - window`` 的记录，其余在推进水位时清理并计入
    ``expired``。水位下界之外再到达的消息记为 ``too_late`` 直接丢弃。

落盘口径
    ``snapshot.json`` 为全量快照，``journal.jsonl`` 为逐条增量日志。每条日志
    带自增 ``seq``，快照记录已应用的 ``seq``；恢复时先读快照，再重放
    ``seq > snapshot.seq`` 的日志，因此重复重放是幂等的。``flush()`` 写快照并
    清空日志。
"""

from __future__ import annotations

import json
import os
import threading

FORMAT = 1
DEFAULT_WINDOW = 60.0
SNAPSHOT_NAME = "snapshot.json"
JOURNAL_NAME = "journal.jsonl"

_UNSET = object()

_lock = threading.RLock()
_state: dict = {}
_watermark = None
_window = DEFAULT_WINDOW
_window_explicit = False
_state_path = None
_seq = 0
_journal_pending = 0
_loaded = False
_loading = False
_stats = {"accepted": 0, "dropped": 0, "duplicates": 0, "expired": 0, "too_late": 0}


def _empty_stats():
    return {"accepted": 0, "dropped": 0, "duplicates": 0, "expired": 0, "too_late": 0}


# 环境变量只作为初始配置；显式 configure() 优先。
if os.environ.get("METRICDEDUP2_STATE_PATH"):
    _state_path = os.environ["METRICDEDUP2_STATE_PATH"]
if os.environ.get("METRICDEDUP2_WINDOW"):
    _window = float(os.environ["METRICDEDUP2_WINDOW"])
    _window_explicit = True


def _snapshot_path():
    return os.path.join(_state_path, SNAPSHOT_NAME)


def _journal_path():
    return os.path.join(_state_path, JOURNAL_NAME)


def _ensure_state_dir():
    os.makedirs(_state_path, exist_ok=True)


def _norm_version(version):
    """把版本号归一化为可比较的元组，兼容数字与字符串。"""
    if version is None:
        return (-1, 0.0)
    if isinstance(version, bool):
        return (0, float(version))
    if isinstance(version, (int, float)):
        return (0, float(version))
    return (1, str(version))


def _order(entry):
    """同键比较键：event_time 优先，version 次级；无时间戳的排最后。"""
    event_time = entry.get("event_time")
    version_key = _norm_version(entry.get("version"))
    if event_time is None:
        return (0, 0.0, version_key)
    return (1, float(event_time), version_key)


def _normalize(metric):
    if not isinstance(metric, dict):
        raise TypeError("metric 必须是 dict")
    entry = dict(metric)
    entry.setdefault("key", "")
    entry.setdefault("version", None)
    event_time = entry.get("event_time")
    if event_time is not None and (
        isinstance(event_time, bool) or not isinstance(event_time, (int, float))
    ):
        raise ValueError("event_time 必须是数字或 None")
    entry["event_time"] = None if event_time is None else float(event_time)
    return entry


def _journal(record):
    """追加一条增量日志；未开启落盘或正在重放时为空操作。"""
    global _seq, _journal_pending
    if _loading or not _state_path:
        return
    _seq += 1
    record = dict(record, seq=_seq)
    _ensure_state_dir()
    with open(_journal_path(), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    _journal_pending += 1


def _expire():
    """清理水位窗口外的记录，返回本次清理条数。"""
    if _watermark is None or not _state:
        return 0
    cutoff = _watermark - _window
    expired = [
        key
        for key, entry in _state.items()
        if entry.get("event_time") is not None and entry["event_time"] < cutoff
    ]
    for key in expired:
        del _state[key]
    _stats["expired"] += len(expired)
    return len(expired)


def _advance_watermark(event_time):
    """单调推进水位，返回由此触发的过期条数。"""
    global _watermark
    if event_time is None:
        return 0
    if _watermark is not None and event_time <= _watermark:
        return 0
    _journal({"op": "watermark", "watermark": event_time})
    _watermark = event_time
    return _expire()


def _result(key, entry, accepted, reason, expired):
    current = _state.get(key)
    return {
        "key": key,
        "version": entry["version"],
        "event_time": entry["event_time"],
        "accepted": accepted,
        "reason": reason,
        "dropped": 1 if reason == "stale" else 0,
        "duplicate": 1 if reason == "duplicate" else 0,
        "too_late": 1 if reason == "too_late" else 0,
        "expired": expired,
        "size": len(_state),
        "watermark": _watermark,
        "window": _window,
        "current_version": current["version"] if current else None,
        "current_event_time": current["event_time"] if current else None,
    }


def put(metric):
    """写入一条指标，同键保留最高版本；返回本次处理结果与状态统计。"""
    with _lock:
        _ensure_loaded()
        entry = _normalize(metric)
        key = entry["key"]
        event_time = entry["event_time"]

        if (
            event_time is not None
            and _watermark is not None
            and event_time < _watermark - _window
        ):
            _journal(
                {
                    "op": "drop",
                    "reason": "too_late",
                    "key": key,
                    "version": entry["version"],
                    "event_time": event_time,
                }
            )
            _stats["too_late"] += 1
            return _result(key, entry, False, "too_late", 0)

        expired = _advance_watermark(event_time)
        current = _state.get(key)

        if current is None or _order(entry) > _order(current):
            _journal({"op": "put", "key": key, "entry": entry})
            _state[key] = entry
            _stats["accepted"] += 1
            return _result(key, entry, True, "accepted", expired)

        if _order(entry) == _order(current):
            # 同键同 (event_time, version)：重复消息，幂等处理。
            _journal(
                {
                    "op": "drop",
                    "reason": "duplicate",
                    "key": key,
                    "version": entry["version"],
                    "event_time": event_time,
                }
            )
            _stats["duplicates"] += 1
            return _result(key, entry, False, "duplicate", expired)

        _journal(
            {
                "op": "drop",
                "reason": "stale",
                "key": key,
                "version": entry["version"],
                "event_time": event_time,
            }
        )
        _stats["dropped"] += 1
        return _result(key, entry, False, "stale", expired)


def flush(watermark=_UNSET):
    """清理窗口、输出统计，并在开启落盘时写全量快照、清空增量日志。

    传入 ``watermark`` 可强制推进水位（只允许前推）。
    """
    global _journal_pending
    with _lock:
        _ensure_loaded()
        expired = 0
        if watermark is not _UNSET:
            target = float(watermark)
            if _watermark is not None and target < _watermark:
                raise ValueError("flush(watermark=...) 不能回退水位")
            expired += _advance_watermark(target)
        expired += _expire()

        pending = _journal_pending
        persisted = _write_snapshot()
        return {
            "flushed": pending if persisted else 0,
            "expired": expired,
            "size": len(_state),
            "watermark": _watermark,
            "window": _window,
            "accepted": _stats["accepted"],
            "dropped": _stats["dropped"],
            "duplicates": _stats["duplicates"],
            "too_late": _stats["too_late"],
            "persisted": persisted,
        }


def stats():
    """当前状态与累计统计，按字段输出。"""
    with _lock:
        _ensure_loaded()
        result = {
            "size": len(_state),
            "watermark": _watermark,
            "window": _window,
            "state_path": _state_path,
        }
        result.update(_stats)
        return result


def configure(*, state_path=_UNSET, window=_UNSET, reload=False):
    """配置落盘目录和窗口长度。

    ``state_path`` 变化或 ``reload=True`` 时会重新从磁盘装载状态，
    用于进程重启后的恢复；``window`` 变更只影响后续清理。
    """
    global _state_path, _window, _window_explicit, _loaded
    with _lock:
        path_changed = state_path is not _UNSET and state_path != _state_path
        if state_path is not _UNSET:
            _state_path = state_path
        if window is not _UNSET:
            _window = float(window)
            _window_explicit = True
        if path_changed or reload or not _loaded:
            _loaded = False
            _load()
        else:
            _expire()
        return {
            "state_path": _state_path,
            "window": _window,
            "watermark": _watermark,
            "size": len(_state),
        }


def reset():
    """清空内存状态与统计；开启落盘时同时写空快照、清空日志。"""
    global _loaded
    with _lock:
        _reset_memory()
        _loaded = True
        if _state_path:
            _write_snapshot()
        return stats()


def _reset_memory():
    global _state, _watermark, _seq, _journal_pending, _stats
    _state = {}
    _watermark = None
    _seq = 0
    _journal_pending = 0
    _stats = _empty_stats()


def _apply_entry(entry):
    """重放一条已接受记录（不写日志），保证幂等。"""
    key = entry.get("key", "")
    current = _state.get(key)
    if current is None or _order(entry) > _order(current):
        _state[key] = dict(entry)
        _stats["accepted"] += 1
    elif _order(entry) == _order(current):
        _state[key] = dict(entry)


def _replay(record):
    global _watermark
    op = record.get("op")
    if op == "put":
        entry = dict(record.get("entry") or {})
        entry.setdefault("key", record.get("key", ""))
        entry.setdefault("version", record.get("version"))
        entry.setdefault("event_time", record.get("event_time"))
        _apply_entry(entry)
    elif op == "drop":
        reason = record.get("reason")
        if reason == "duplicate":
            _stats["duplicates"] += 1
        elif reason == "too_late":
            _stats["too_late"] += 1
        else:
            _stats["dropped"] += 1
    elif op == "watermark":
        target = record.get("watermark")
        if target is not None:
            target = float(target)
            if _watermark is None or target > _watermark:
                _watermark = target


def _load():
    """从全量快照 + 增量日志恢复状态与水位。"""
    global _watermark, _window, _seq, _journal_pending, _loaded, _loading
    _reset_memory()
    if _state_path is None:
        _loaded = True
        return
    _ensure_state_dir()

    if os.path.exists(_snapshot_path()):
        with open(_snapshot_path(), encoding="utf-8") as handle:
            snapshot = json.load(handle)
        for entry in snapshot.get("entries") or []:
            if isinstance(entry, dict):
                entry.setdefault("event_time", None)
                entry.setdefault("version", None)
                _state[entry.get("key", "")] = entry
        _watermark = snapshot.get("watermark")
        _seq = int(snapshot.get("seq", 0))
        saved_stats = snapshot.get("stats") or {}
        for field in _stats:
            _stats[field] = int(saved_stats.get(field, 0))
        if not _window_explicit and snapshot.get("window") is not None:
            _window = float(snapshot["window"])

    if os.path.exists(_journal_path()):
        _loading = True
        try:
            with open(_journal_path(), encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        # 崩溃时可能留下半行，跳过。
                        continue
                    seq = int(record.get("seq", 0))
                    if seq <= _seq:
                        continue
                    _replay(record)
                    if seq > _seq:
                        _seq = seq
        finally:
            _loading = False

    _expire()
    _journal_pending = 0
    _loaded = True


def _write_snapshot():
    """写全量快照并清空增量日志，返回是否落盘。"""
    global _journal_pending
    if not _state_path:
        return False
    _ensure_state_dir()
    payload = {
        "format": FORMAT,
        "seq": _seq,
        "watermark": _watermark,
        "window": _window,
        "stats": dict(_stats),
        "entries": list(_state.values()),
    }
    temporary = _snapshot_path() + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, _snapshot_path())
    try:
        with open(_journal_path(), "w", encoding="utf-8"):
            pass
    except OSError:
        # 快照已带上 seq，日志即便没清空，重放时也会被跳过。
        pass
    _journal_pending = 0
    return True


def _ensure_loaded():
    if not _loaded:
        _load()
