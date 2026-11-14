"""模块级暂停/恢复门面，签名保持 pause(task) / resume(task) 不变。

底层是一个默认 :class:`~crawlpause.coordinator.Coordinator` 单例，
多任务各自独立阶段；需要隔离（如测试）时直接实例化 Coordinator。
"""

from .coordinator import get_coordinator
from .errors import (
    CheckpointError,
    CrawlPauseError,
    TaskExecutionError,
    TaskNotPausedError,
    TaskStateError,
)


def pause(task, drain_timeout=5.0):
    """暂停指定任务：置只读 -> 等当前批次收尾 -> 超时中断 -> 落检查点。

    幂等：任务已暂停时重复调用返回同一结果，不改变阶段。
    """
    return get_coordinator().pause(task, drain_timeout=drain_timeout)


def resume(task):
    """从检查点恢复指定任务。

    幂等：已在运行的任务重复调用返回同一恢复结果，不改变阶段；
    从未暂停或未知任务抛 :class:`TaskNotPausedError`。
    """
    return get_coordinator().resume(task)


def is_paused(task):
    """任务是否处于暂停相关阶段（pausing/paused）。"""
    return get_coordinator().is_paused(task)


__all__ = [
    "pause",
    "resume",
    "is_paused",
    "CrawlPauseError",
    "TaskNotPausedError",
    "TaskStateError",
    "CheckpointError",
    "TaskExecutionError",
]
