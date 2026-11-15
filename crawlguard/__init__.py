"""crawlguard：采集任务准入。

模块级快捷函数基于一个默认准入器（``admit`` / ``reload`` 接口保持不变）；
需要多实例或可控时钟时直接用 ``Guard``。
"""

from .guard import (
    Guard,
    ListError,
    admit,
    audit_log,
    configure,
    export_rejections,
    reload,
    reload_history,
    reset,
    status,
    summary,
)

__all__ = [
    "Guard",
    "ListError",
    "admit",
    "reload",
    "reload_history",
    "configure",
    "reset",
    "status",
    "summary",
    "audit_log",
    "export_rejections",
]
