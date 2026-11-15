"""retryquota：站点 + 全局两级重试配额协调。

模块级默认协调器保持 ``request(task, site)`` / ``release(task)`` 接口，
额外提供 ``fail``、``set_priority``、``sweep_expired``、``snapshot``、
``events``、``configure`` 以及可实例化的 :class:`QuotaCoordinator`。
"""

from .quota import (  # noqa: F401
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_SITE_LIMIT,
    QuotaCoordinator,
    configure,
    events,
    fail,
    get_coordinator,
    read_state,
    release,
    request,
    reset,
    set_priority,
    snapshot,
    sweep_expired,
)

__all__ = [
    "QuotaCoordinator",
    "configure",
    "events",
    "fail",
    "get_coordinator",
    "read_state",
    "release",
    "request",
    "reset",
    "set_priority",
    "snapshot",
    "sweep_expired",
    "DEFAULT_GLOBAL_LIMIT",
    "DEFAULT_SITE_LIMIT",
    "DEFAULT_LEASE_SECONDS",
]
