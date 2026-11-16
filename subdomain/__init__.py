"""子域枚举收敛小工具：入口归并、泛解析识别、无效项跳过、多轮对比。"""

from .resolve import (
    DEFAULT_WILDCARD_THRESHOLD,
    diff,
    enumerate,
    load,
    report,
    reset,
    restore,
)

__all__ = [
    "load",
    "enumerate",
    "report",
    "diff",
    "restore",
    "reset",
    "DEFAULT_WILDCARD_THRESHOLD",
]
