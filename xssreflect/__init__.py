"""反射型 / 存储型 XSS 检测小工具。

按回显位置（HTML 正文、属性、脚本、URL）判断编码后能否执行，
编码内容先还原再判断，同页重复回显合并，拿不准的留待人工确认。
"""

from .reflect import (
    PAYLOAD,
    CONTEXT_HTML,
    CONTEXT_ATTR,
    CONTEXT_SCRIPT,
    CONTEXT_URL,
    VERDICT_HIT,
    VERDICT_SAFE,
    VERDICT_REVIEW,
    ReflectEngine,
    load,
    unload,
    add_entry,
    remove_entry,
    check,
    check_all,
    report,
    reset,
)

__all__ = [
    "PAYLOAD",
    "CONTEXT_HTML",
    "CONTEXT_ATTR",
    "CONTEXT_SCRIPT",
    "CONTEXT_URL",
    "VERDICT_HIT",
    "VERDICT_SAFE",
    "VERDICT_REVIEW",
    "ReflectEngine",
    "load",
    "unload",
    "add_entry",
    "remove_entry",
    "check",
    "check_all",
    "report",
    "reset",
]
