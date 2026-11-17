"""反射型 / 存储型 XSS 检测小工具。"""

from .reflect import (
    CANARY,
    PAYLOAD,
    check,
    load,
    report,
    reset,
    submit,
)

__all__ = [
    "CANARY",
    "PAYLOAD",
    "check",
    "load",
    "report",
    "reset",
    "submit",
]
