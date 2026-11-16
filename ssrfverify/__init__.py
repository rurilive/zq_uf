"""SSRF 回连验证工具。

对外入口：

``check(url, param)``
    注入带唯一标识的回连 payload 并发起请求，返回命中标识、回连时间、
    目标地址以及命中/可疑/未命中结论。
``report()``
    汇总本轮结论，命中项与接收端回连记录按标识逐条对应。
``configure(receiver=..., timeout=...)``
    指定接收端（例如能对外暴露的自建接收端）或超时。

接收端不可用时 ``check`` 抛 :class:`ReceiverUnavailable` 并停止本轮。
仅用于已获授权的安全测试。
"""

from .errors import ReceiverUnavailable, SsrfVerifyError
from .probe import (
    HttpResponse,
    callbacks,
    check,
    configure,
    report,
    reset,
    shutdown,
)
from .receiver import BaseReceiver, Callback, EmbeddedReceiver

__all__ = [
    "BaseReceiver",
    "Callback",
    "EmbeddedReceiver",
    "HttpResponse",
    "ReceiverUnavailable",
    "SsrfVerifyError",
    "callbacks",
    "check",
    "configure",
    "report",
    "reset",
    "shutdown",
]
