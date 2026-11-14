from .sched import (
    Scheduler,
    SchedulerError,
    complete,
    configure,
    next_task,
    preempt,
    reset,
    restore,
    snapshot,
    stats,
    submit,
)

# next() 是 next_task() 的短名别名，两个接口等价。
next = next_task

__all__ = [
    "Scheduler",
    "SchedulerError",
    "complete",
    "configure",
    "next",
    "next_task",
    "preempt",
    "reset",
    "restore",
    "snapshot",
    "submit",
    "stats",
]
