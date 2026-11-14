"""crawlpause —— 采集任务的按任务暂停/恢复、检查点与幂等入库。"""

from .coordinator import (
    RUNNING,
    PAUSING,
    PAUSED,
    RESUMING,
    FAILED,
    Coordinator,
    CrawlTask,
    get_coordinator,
)
from .errors import (
    CheckpointError,
    CrawlPauseError,
    TaskExecutionError,
    TaskNotPausedError,
    TaskStateError,
)
from .pause import is_paused, pause, resume
from .store import IdempotentStore

__all__ = [
    "pause",
    "resume",
    "is_paused",
    "Coordinator",
    "CrawlTask",
    "IdempotentStore",
    "get_coordinator",
    "RUNNING",
    "PAUSING",
    "PAUSED",
    "RESUMING",
    "FAILED",
    "CrawlPauseError",
    "TaskNotPausedError",
    "TaskStateError",
    "CheckpointError",
    "TaskExecutionError",
]
