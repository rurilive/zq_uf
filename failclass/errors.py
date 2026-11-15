"""抓取失败的归一与分类。

所有失败先归一到四种原因之一：

``timeout``
    连接 / 读取超时，值得等一会儿再试。
``blocked``
    被站点封禁或限流（403、429、验证码等），继续打会连出口一起封。
``not_found``
    页面不存在（404 / 410），重试没有意义。
``error``
    其余暂未识别的失败，按普通重试处理。

分类时会把当时的状态码、响应头、请求标识一并放进 ``evidence``，
方便事后追溯到具体请求。
"""

from __future__ import annotations

TIMEOUT = "timeout"
BLOCKED = "blocked"
NOT_FOUND = "not_found"
ERROR = "error"

#: 归一后的全部原因，顺序稳定，便于统计和展示。
REASONS = (TIMEOUT, BLOCKED, NOT_FOUND, ERROR)

_ALIASES = {
    "timeout": TIMEOUT,
    "timedout": TIMEOUT,
    "time-out": TIMEOUT,
    "time_out": TIMEOUT,
    "slow": TIMEOUT,
    "blocked": BLOCKED,
    "ban": BLOCKED,
    "banned": BLOCKED,
    "forbidden": BLOCKED,
    "rate_limited": BLOCKED,
    "rate-limited": BLOCKED,
    "ratelimit": BLOCKED,
    "captcha": BLOCKED,
    "not_found": NOT_FOUND,
    "not-found": NOT_FOUND,
    "notfound": NOT_FOUND,
    "missing": NOT_FOUND,
    "gone": NOT_FOUND,
    "http_404": NOT_FOUND,
    "http_410": NOT_FOUND,
    "error": ERROR,
    "unknown": ERROR,
    "other": ERROR,
    "": ERROR,
}

# 只看状态码就能确定的原因。
_NOT_FOUND_STATUS = frozenset({404, 410})
_BLOCKED_STATUS = frozenset({403, 429, 451})
_TIMEOUT_STATUS = frozenset({408, 504, 524, 598, 599})

_TIMEOUT_SIGNALS = (
    "timeout",
    "timed out",
    "read timeout",
    "connect timeout",
    "deadline exceeded",
    "etimedout",
    "超时",
)
_BLOCKED_SIGNALS = (
    "banned",
    "blocked",
    "forbidden",
    "rate limit",
    "ratelimit",
    "too many requests",
    "captcha",
    "access denied",
    "封禁",
    "拒绝访问",
    "429",
    "403",
)
_NOT_FOUND_SIGNALS = (
    "not found",
    "does not exist",
    "no longer available",
    "404",
    "410",
    "不存在",
    "页面不存在",
)


def normalize_reason(value):
    """把原因别名归一成 :data:`REASONS` 里的一个值。"""
    if isinstance(value, dict):
        value = value.get("reason")
    if value is None:
        return ERROR
    key = str(value).strip().lower().replace(" ", "_")
    return _ALIASES.get(key, ERROR)


def _detail(error):
    """取一段可读的失败描述。"""
    if error is None:
        return ""
    if isinstance(error, str):
        return error
    if isinstance(error, BaseException):
        return str(error) or type(error).__name__
    if isinstance(error, dict):
        for key in ("message", "detail", "error", "reason"):
            if error.get(key):
                return str(error[key])
        return str(error)
    return str(error)


def _extract_response(response):
    """兼容 ``requests`` 风格对象、``SimpleNamespace`` 和普通 dict。"""
    if response is None:
        return None, {}
    if isinstance(response, dict):
        status = response.get("status", response.get("status_code"))
        headers = response.get("headers") or {}
    elif isinstance(response, (int, str)) and str(response).strip().isdigit():
        status, headers = response, {}
    else:
        status = getattr(response, "status_code", None)
        if status is None:
            status = getattr(response, "status", None)
        headers = getattr(response, "headers", None) or {}
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    try:
        headers = dict(headers)
    except (TypeError, ValueError):
        headers = {}
    return status, headers


def _lower_headers(headers):
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _headers_signal_block(headers):
    """只认明确的封禁/限流响应头，避免把普通 CDN 流量当成封禁。"""
    low = _lower_headers(headers)
    if "cf-mitigated" in low:
        return True
    if low.get("x-ratelimit-remaining") == "0":
        return True
    return False


def _status_reason(status, headers, detail):
    """状态码优先；返回 ``None`` 表示状态码没有给出结论。"""
    if status is None:
        return None
    if status in _NOT_FOUND_STATUS:
        return NOT_FOUND
    if status in _BLOCKED_STATUS:
        return BLOCKED
    if status in _TIMEOUT_STATUS:
        return TIMEOUT
    if status == 503 and (_headers_signal_block(headers) or _text_reason(detail) == BLOCKED):
        return BLOCKED
    if 400 <= status < 600:
        return ERROR
    return None


def _is_timeout(error):
    if isinstance(error, TimeoutError):
        return True
    return "timeout" in type(error).__name__.lower()


def _text_reason(text):
    value = (text or "").lower()
    if not value:
        return None
    for signal in _TIMEOUT_SIGNALS:
        if signal in value:
            return TIMEOUT
    for signal in _BLOCKED_SIGNALS:
        if signal in value:
            return BLOCKED
    for signal in _NOT_FOUND_SIGNALS:
        if signal in value:
            return NOT_FOUND
    return None


def classify(error, response=None, *, request_id=None, url=None):
    """把一个抓取失败归一成 ``{"reason", "status", "headers", "evidence"}``。

    ``error`` 可以是异常、字符串、状态码或带 ``status`` / ``headers`` 的 dict；
    ``response`` 支持 ``requests`` 风格对象或同名字典。响应里的
    ``status`` / ``headers`` 会原样保留在证据中。
    """
    status, headers = _extract_response(response)

    # 有些库会把响应挂在异常上，例如 requests.HTTPError.response。
    if response is None and isinstance(error, BaseException):
        attached = getattr(error, "response", None)
        if attached is not None:
            status, headers = _extract_response(attached)

    if isinstance(error, dict):
        if status is None:
            status = error.get("status", error.get("status_code"))
        if not headers:
            headers = error.get("headers") or {}
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        try:
            headers = dict(headers)
        except (TypeError, ValueError):
            headers = {}
    elif isinstance(error, int) and status is None:
        status = error

    detail = _detail(error)
    error_type = type(error).__name__ if isinstance(error, BaseException) else None

    reason = _status_reason(status, headers, detail)
    if reason is None and _is_timeout(error):
        reason = TIMEOUT
    if reason is None:
        reason = _text_reason(detail)
    if reason is None:
        reason = ERROR

    evidence = {
        "detail": detail,
        "error_type": error_type,
        "status": status,
        "headers": headers,
        "request_id": request_id,
        "url": url,
    }
    return {
        "reason": reason,
        "status": status,
        "headers": headers,
        "evidence": evidence,
    }
