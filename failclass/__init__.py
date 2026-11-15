"""failclass — 抓取失败归一分类与按站点共享的重试预算。"""

from .budget import FailureGovernor
from .errors import (
    BLOCKED,
    ERROR,
    NOT_FOUND,
    REASONS,
    TIMEOUT,
    classify,
    normalize_reason,
)
from .policy import policy

#: 一次失败按站点记账的默认入口别名。
Governor = FailureGovernor

__all__ = [
    "BLOCKED",
    "ERROR",
    "NOT_FOUND",
    "REASONS",
    "TIMEOUT",
    "FailureGovernor",
    "Governor",
    "classify",
    "normalize_reason",
    "policy",
]
