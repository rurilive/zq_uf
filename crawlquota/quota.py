"""采集进程资源配额：运行时可调上限，超额排队等待，按任务记账。"""

import math
import threading
import time

CAPS = {"thread": 32, "fd": 256, "memory_mb": 2048}
WAIT_TIMEOUT = 1.0

_DEFAULT_CAPS = dict(CAPS)
_DEFAULT_WAIT_TIMEOUT = WAIT_TIMEOUT


class QuotaError(RuntimeError):
    """配额相关的明确错误（如申请量超过上限本身，永远无法满足）。"""


class QuotaTimeoutError(QuotaError):
    """排队等待配额超时。"""


_cond = threading.Condition()
_used = {kind: 0 for kind in CAPS}
_tasks = {}
_wait_seconds = {}
_wait_count = {}


def _check_kind(kind):
    if kind not in CAPS:
        raise KeyError(f"未知配额类型: {kind!r}，可选: {sorted(CAPS)}")


def _check_amount(amount):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValueError(f"amount 必须是正数，收到 {amount!r}")
    if amount <= 0:
        raise ValueError(f"amount 必须大于 0，收到 {amount!r}")


def _check_timeout(timeout):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(f"timeout 必须是非负秒数，收到 {timeout!r}")
    if timeout < 0:
        raise ValueError(f"timeout 不能为负，收到 {timeout!r}")


def acquire(task, kind, amount=1, timeout=None):
    """占用 amount 额度，返回占用后的全局用量。

    额度不足时排队等待，直到有释放或上限被调大；超过 timeout 秒
    （默认 WAIT_TIMEOUT）抛 QuotaTimeoutError。amount 超过上限本身
    永远无法满足，立即抛 QuotaError。timeout 传 float("inf") 表示
    无限等待。
    """
    _check_kind(kind)
    _check_amount(amount)
    with _cond:
        if amount > CAPS[kind]:
            raise QuotaError(
                f"任务 {task!r} 申请 {kind} 额度 {amount} 超过上限 "
                f"{CAPS[kind]}，永远无法满足"
            )
        limit = WAIT_TIMEOUT if timeout is None else timeout
        if limit is not None:
            _check_timeout(limit)
            if math.isinf(limit):
                limit = None
        deadline = None if limit is None else time.monotonic() + limit
        waited = 0.0
        try:
            while _used[kind] + amount > CAPS[kind]:
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise QuotaTimeoutError(
                            f"任务 {task!r} 等待 {kind} 额度 {amount} 超时 "
                            f"({limit}s)，当前占用 {_used[kind]}/{CAPS[kind]}"
                        )
                start = time.monotonic()
                _cond.wait(remaining)
                waited += time.monotonic() - start
        finally:
            if waited > 0:
                _wait_seconds[task] = _wait_seconds.get(task, 0.0) + waited
                _wait_count[task] = _wait_count.get(task, 0) + 1
        _used[kind] += amount
        slot = _tasks.setdefault(task, {key: 0 for key in CAPS})
        slot[kind] += amount
        return _used[kind]


def release(task, kind, amount=1):
    """释放额度并唤醒等待者，返回释放后的全局占用。

    超量释放按当前占用截断（用于兜底清理与测试复位），不抛错。
    """
    _check_kind(kind)
    _check_amount(amount)
    with _cond:
        _used[kind] = max(0, _used[kind] - amount)
        slot = _tasks.get(task)
        if slot is not None:
            slot[kind] = max(0, slot[kind] - amount)
        _cond.notify_all()
        return _used[kind]


def release_all(task):
    """任务结束时释放其持有的全部额度，返回各类释放明细。"""
    with _cond:
        freed = {kind: 0 for kind in CAPS}
        slot = _tasks.get(task)
        if slot is not None:
            for kind in CAPS:
                amount = min(slot[kind], _used[kind])
                _used[kind] -= amount
                freed[kind] = amount
                slot[kind] -= amount
        _cond.notify_all()
        return freed


def set_cap(kind, value):
    """运行时调整某类配额上限并唤醒等待者重新检查，返回新上限。"""
    _check_kind(kind)
    _check_amount(value)
    with _cond:
        CAPS[kind] = value
        _cond.notify_all()
        return CAPS[kind]


def set_wait_timeout(seconds):
    """设置默认等待超时（秒），None 表示默认无限等待。"""
    global WAIT_TIMEOUT
    if seconds is not None:
        _check_timeout(seconds)
    WAIT_TIMEOUT = seconds


def usage():
    """全局各类配额当前占用。"""
    with _cond:
        return dict(_used)


def task_usage(task=None):
    """按任务查看当前持有额度；task 为 None 时返回全部任务的快照。"""
    with _cond:
        if task is None:
            return {name: dict(slot) for name, slot in _tasks.items()}
        return dict(_tasks.get(task, {kind: 0 for kind in CAPS}))


def wait_stats(task=None):
    """按任务查看等待统计：wait_seconds 累计等待秒数，wait_count 排队次数。"""
    with _cond:
        if task is not None:
            return {
                "wait_seconds": _wait_seconds.get(task, 0.0),
                "wait_count": _wait_count.get(task, 0),
            }
        return {
            name: {
                "wait_seconds": _wait_seconds.get(name, 0.0),
                "wait_count": _wait_count.get(name, 0),
            }
            for name in set(_wait_seconds) | set(_wait_count)
        }


def stats():
    """汇总快照：上限、全局占用、各任务占用与等待时长。"""
    with _cond:
        names = set(_tasks) | set(_wait_seconds) | set(_wait_count)
        return {
            "caps": dict(CAPS),
            "used": dict(_used),
            "tasks": {
                name: {
                    "used": dict(_tasks.get(name, {key: 0 for key in CAPS})),
                    "wait_seconds": _wait_seconds.get(name, 0.0),
                    "wait_count": _wait_count.get(name, 0),
                }
                for name in sorted(names)
            },
        }


def reset():
    """清空全部占用、任务记账与等待统计，恢复默认上限和默认超时。"""
    global WAIT_TIMEOUT
    with _cond:
        CAPS.update(_DEFAULT_CAPS)
        WAIT_TIMEOUT = _DEFAULT_WAIT_TIMEOUT
        for kind in _used:
            _used[kind] = 0
        _tasks.clear()
        _wait_seconds.clear()
        _wait_count.clear()
        _cond.notify_all()
