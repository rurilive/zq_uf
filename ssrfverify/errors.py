"""异常类型。"""


class SsrfVerifyError(Exception):
    """本项目所有异常的基类。"""


class ReceiverUnavailable(SsrfVerifyError):
    """回连接收端不可用：本轮必须中止，不能把「没有回连」当成安全。"""
