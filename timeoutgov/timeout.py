"""任务超时治理：动态超时、现场保存、断点恢复、统计输出。

task 字段：
  name       必填，非空字符串
  work       必填，可调用；无断点签名 work()，断点恢复签名 work(checkpoint)
  type       可选，任务类型，动态超时按类型统计历史耗时
  progress   可选，dict，work 执行中就地更新，超时时作为现场保存
  checkpoint 可选，上次超时保存的断点，传入后调用 work(checkpoint)
"""

import inspect
import json
import sys
import traceback

DEFAULT_TIMEOUT = 30.0   # 无历史数据时的默认超时（秒）
MIN_TIMEOUT = 1.0        # 动态超时下限（秒）
MAX_TIMEOUT = 3600.0     # 动态超时上限（秒）
HEADROOM_FACTOR = 1.5    # 均值余量系数
STDDEV_FACTOR = 3.0      # 标准差倍数
DEFAULT_TYPE = "default"

_clock = None
_attempts = []
_history = {}      # type -> [elapsed, ...]
_checkpoints = {}  # name -> 现场 dict
_timeouts = []     # 超时事件
_recoveries = []   # 恢复事件


class TaskTimeoutError(TimeoutError):
    """超时异常，携带现场：limit/elapsed/checkpoint。"""

    def __init__(self, name, limit, elapsed, checkpoint):
        self.task_name = name
        self.limit = limit
        self.elapsed = elapsed
        self.checkpoint = checkpoint
        super().__init__(
            f"task {name} exceeded {limit}s (elapsed {elapsed}s); "
            f"checkpoint saved, resume with task['checkpoint']"
        )


def set_clock(func):
    global _clock
    _clock = func


def reset_state():
    """清空历史、现场与统计（时钟注入不受影响）。"""
    _attempts.clear()
    _history.clear()
    _checkpoints.clear()
    _timeouts.clear()
    _recoveries.clear()


def _now():
    return _clock() if _clock else 0


def _jsonable(value, what):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be JSON-serializable, got {type(value).__name__}")


def _validate(task, deadline):
    if not isinstance(task, dict):
        raise TypeError(f"task must be a dict, got {type(task).__name__}")
    name = task.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("task requires a non-empty string 'name'")
    if not callable(task.get("work")):
        raise TypeError(f"task {name}: 'work' must be callable")
    task_type = task.get("type", DEFAULT_TYPE)
    if not isinstance(task_type, str) or not task_type:
        raise ValueError(f"task {name}: 'type' must be a non-empty string")
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise TypeError(f"task {name}: deadline must be a number, got {type(deadline).__name__}")
        if deadline <= 0:
            raise ValueError(f"task {name}: deadline must be positive, got {deadline}")
    progress = task.get("progress")
    if progress is not None and not isinstance(progress, dict):
        raise TypeError(f"task {name}: 'progress' must be a dict, got {type(progress).__name__}")
    checkpoint = task.get("checkpoint")
    if checkpoint is not None:
        _jsonable(checkpoint, f"task {name}: 'checkpoint'")
        try:
            params = inspect.signature(task["work"]).parameters
        except (TypeError, ValueError):
            params = None
        if params is not None:
            accepts = any(
                p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
                for p in params.values()
            )
            if not accepts:
                raise ValueError(
                    f"task {name}: checkpoint provided but work() takes no arguments"
                )
    return task_type


def _dynamic_limit(task_type):
    samples = _history.get(task_type)
    if not samples:
        return DEFAULT_TIMEOUT
    mean = sum(samples) / len(samples)
    std = 0.0
    if len(samples) > 1:
        var = sum((s - mean) ** 2 for s in samples) / len(samples)
        std = var ** 0.5
    limit = mean * HEADROOM_FACTOR + std * STDDEV_FACTOR
    return min(MAX_TIMEOUT, max(MIN_TIMEOUT, limit))


def _capture_checkpoint(task, task_type, elapsed, limit, frame):
    progress = task.get("progress")
    snapshot = dict(progress) if progress is not None else None
    if snapshot is not None:
        _jsonable(snapshot, f"task {task['name']}: progress snapshot")
    if frame is not None:
        stack = traceback.format_stack(frame)
        code = frame.f_code
        last_frame = {"func": code.co_name, "file": code.co_filename,
                      "line": frame.f_lineno}
    else:
        stack = traceback.format_stack()
        last_frame = None
    return {
        "task": task["name"],
        "type": task_type,
        "progress": snapshot,
        "stack": stack,
        "last_frame": last_frame,
        "elapsed": elapsed,
        "limit": limit,
        "at": _now(),
    }


def run(task, deadline=None):
    """执行 task，超时抛 TaskTimeoutError 并保存现场；带 checkpoint 时断点续跑。

    deadline 显式指定时覆盖动态超时；否则按任务类型的历史耗时计算：
    limit = mean * HEADROOM_FACTOR + std * STDDEV_FACTOR，夹在 [MIN, MAX]。
    """
    task_type = _validate(task, deadline)
    name = task["name"]
    limit = float(deadline) if deadline is not None else _dynamic_limit(task_type)
    work = task["work"]
    checkpoint = task.get("checkpoint")
    resumed = checkpoint is not None

    started = _now()
    last_frame = [None]

    def profiler(frame, event, arg):
        if event in ("call", "line"):
            last_frame[0] = frame

    previous = sys.getprofile()
    sys.setprofile(profiler)
    try:
        result = work(checkpoint) if resumed else work()
    finally:
        sys.setprofile(previous)
    elapsed = _now() - started

    if elapsed > limit:
        saved = _capture_checkpoint(task, task_type, elapsed, limit, last_frame[0])
        _checkpoints[name] = saved
        _timeouts.append({
            "name": name, "type": task_type,
            "limit": limit, "elapsed": elapsed, "at": _now(),
        })
        raise TaskTimeoutError(name, limit, elapsed, saved)

    _attempts.append({"name": name, "type": task_type, "started": started, "elapsed": elapsed})
    _history.setdefault(task_type, []).append(elapsed)
    out = {"result": result, "attempts": len(_attempts)}
    if resumed:
        _recoveries.append({
            "name": name, "type": task_type,
            "resumed_from": checkpoint, "elapsed": elapsed,
        })
        _checkpoints.pop(name, None)
        out["resumed"] = True
    return out


def stats():
    """超时任务、各类型耗时分布、恢复结果。"""
    durations = {}
    for task_type, samples in sorted(_history.items()):
        mean = sum(samples) / len(samples)
        var = sum((s - mean) ** 2 for s in samples) / len(samples)
        durations[task_type] = {
            "count": len(samples),
            "min": min(samples),
            "max": max(samples),
            "mean": mean,
            "pstdev": var ** 0.5,
        }
    return {
        "timeouts": [dict(e) for e in _timeouts],
        "durations": durations,
        "recoveries": [dict(e) for e in _recoveries],
        "attempts": len(_attempts),
        "pending_checkpoints": sorted(_checkpoints),
    }


def dump_state():
    """导出全部状态（JSON 可序列化），便于重启后复现。"""
    return {
        "attempts": [dict(a) for a in _attempts],
        "history": {k: list(v) for k, v in _history.items()},
        "checkpoints": {k: dict(v) for k, v in _checkpoints.items()},
        "timeouts": [dict(e) for e in _timeouts],
        "recoveries": [dict(e) for e in _recoveries],
    }


def load_state(state):
    """从 dump_state() 的导出恢复状态；格式非法时抛错，不静默跳过。"""
    if not isinstance(state, dict):
        raise TypeError(f"state must be a dict, got {type(state).__name__}")
    unknown = set(state) - {"attempts", "history", "checkpoints", "timeouts", "recoveries"}
    if unknown:
        raise ValueError(f"unknown state keys: {sorted(unknown)}")
    history = state.get("history", {})
    if not isinstance(history, dict) or not all(
        isinstance(k, str) and isinstance(v, list) and
        all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
        for k, v in history.items()
    ):
        raise ValueError("state['history'] must be {str: [number, ...]}")
    for key in ("attempts", "timeouts", "recoveries"):
        if not isinstance(state.get(key, []), list):
            raise ValueError(f"state[{key!r}] must be a list")
    if not isinstance(state.get("checkpoints", {}), dict):
        raise ValueError("state['checkpoints'] must be a dict")
    _jsonable(state, "state")
    reset_state()
    _attempts.extend(dict(a) for a in state.get("attempts", []))
    _history.update({k: list(v) for k, v in history.items()})
    _checkpoints.update({k: dict(v) for k, v in state.get("checkpoints", {}).items()})
    _timeouts.extend(dict(e) for e in state.get("timeouts", []))
    _recoveries.extend(dict(e) for e in state.get("recoveries", []))
