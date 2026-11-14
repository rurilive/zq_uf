"""crawlpause 统一异常类型。

所有异常都继承 :class:`CrawlPauseError`，调用方可以按需捕获细分异常，
也可以一把捕获基类。异常消息一律带任务 id 和具体原因。
"""


class CrawlPauseError(Exception):
    """crawlpause 所有异常的基类。"""


class TaskNotPausedError(CrawlPauseError):
    """对从未暂停（或根本不存在）的任务调用 resume。"""


class TaskStateError(CrawlPauseError):
    """任务当前阶段不允许该状态迁移，或重复注册同名任务。"""


class CheckpointError(CrawlPauseError):
    """检查点落盘或读取失败（磁盘、权限、路径非法等）。"""


class TaskExecutionError(CrawlPauseError):
    """任务工作线程在处理某个数据项时抛出异常。"""
