"""错误分类：超时、连接、限流、服务端、永久错误（及未知瞬时错误兜底）。"""
import errno
import http.client
import socket
import ssl


class ErrorClass:
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TRANSIENT = "transient"
    PERMANENT = "permanent"

    RETRYABLE = (TIMEOUT, CONNECTION, RATE_LIMIT, SERVER, TRANSIENT)


class RetryallError(Exception):
    """retryall 自身抛出的异常基类。"""


class PermanentError(RetryallError):
    """显式标记为永久错误，调用方抛出后不会被重试。"""


class HttpError(RetryallError):
    """携带 HTTP 状态码的异常，429/5xx/4xx 据此分类。

    ``retry_after`` 为可选的服务端建议等待秒数（Retry-After）。
    """

    def __init__(self, status, message=None, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(message or "HTTP %s" % status)


def _status_of(exc):
    status = getattr(exc, "status", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    return status if isinstance(status, int) else None


def classify(exc):
    """把异常归入 ``ErrorClass`` 中的某一类。

    永久错误（4xx、:class:`PermanentError`）不参与重试；其余类别按各自
    的策略上限退避重试；无法识别的异常归入 ``transient`` 兜底类别。
    """
    if isinstance(exc, PermanentError):
        return ErrorClass.PERMANENT

    status = _status_of(exc)
    if status is not None:
        if status == 429:
            return ErrorClass.RATE_LIMIT
        if 500 <= status < 600:
            return ErrorClass.SERVER
        if 400 <= status < 500:
            return ErrorClass.PERMANENT

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ErrorClass.TIMEOUT
    if isinstance(exc, ConnectionError):
        return ErrorClass.CONNECTION
    if isinstance(exc, ssl.SSLError):
        if getattr(exc, "errno", None) == errno.ETIMEDOUT:
            return ErrorClass.TIMEOUT
        return ErrorClass.CONNECTION

    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException) and reason is not exc:
        inner = classify(reason)
        if inner != ErrorClass.TRANSIENT:
            return inner

    if isinstance(exc, http.client.HTTPException):
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return ErrorClass.TIMEOUT
        if "disconnected" in name or "notconnected" in name:
            return ErrorClass.CONNECTION

    return ErrorClass.TRANSIENT
