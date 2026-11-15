"""抓取失败归一化与处理策略。

失败原因固定为四类：

``timeout``
    读超时、连接超时、408/504。
``blocked``
    封禁、限流、验证码、407/403/429/451，以及带 Cloudflare/Retry-After 特征的 5xx。
``not_found``
    页面不存在，404/410。永久失败，重试没有意义。
``error``
    其余可重试的普通失败。

分类结果同时附带证据：状态码、响应头快照、URL、错误原文。
"""

from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

TIMEOUT = "timeout"
BLOCKED = "blocked"
NOT_FOUND = "not_found"
ERROR = "error"

REASONS = (TIMEOUT, BLOCKED, NOT_FOUND, ERROR)

_BLOCKED_STATUS = frozenset({403, 407, 429, 451})
_NOT_FOUND_STATUS = frozenset({404, 410})
_TIMEOUT_STATUS = frozenset({408, 504})
_GATEWAY_STATUS = frozenset({502, 503, 520, 521, 522, 523, 524})

_TIMEOUT_PATTERN = re.compile(
    r"\b(?:time(?:d)?\s*out|timeout|deadline exceeded|read timeout|connect timeout|"
    r"ETIMEDOUT)\b|超时",
    re.IGNORECASE,
)
_BLOCKED_PATTERN = re.compile(
    r"\b(?:forbidden|blocked|banned|ban|captcha|rate[\s_-]?limit(?:ed)?|"
    r"too many requests|access denied|unavailable for legal reasons)\b|"
    r"封禁|封号|验证码|限流|拒绝访问",
    re.IGNORECASE,
)
_NOT_FOUND_PATTERN = re.compile(
    r"\b(?:not found|no such (?:page|resource)|gone|404)\b|页面不存在|找不到页面",
    re.IGNORECASE,
)

# 证据里不落盘的敏感头。
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "set-cookie"}
)
_MAX_HEADER_VALUE = 1000
_MAX_MESSAGE = 1000
_MAX_URL = 2000

# 基础策略。实际预算由 FailureRouter 按站点共享。
_POLICIES = {
    TIMEOUT: {"action": "retry", "delay": 5, "budget": "site", "cooldown": 0},
    BLOCKED: {
        "action": "switch_egress",
        "delay": 0,
        "budget": "site",
        "cooldown": 300,
    },
    NOT_FOUND: {"action": "give_up", "delay": 0, "budget": "site", "cooldown": 0},
    ERROR: {"action": "retry", "delay": 1, "budget": "site", "cooldown": 0},
}


def _lookup(obj, names):
    if obj is None:
        return None
    for name in names:
        try:
            if isinstance(obj, dict):
                if name in obj:
                    value = obj[name]
                    if value is not None:
                        return value
            else:
                value = getattr(obj, name, None)
                if value is not None:
                    return value
        except Exception:
            continue
    return None


def _status_code(response):
    if response is None or isinstance(response, bool):
        return None
    if isinstance(response, int):
        return int(response)
    if isinstance(response, str):
        return None
    value = _lookup(response, ("status_code", "status"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _header_items(headers):
    if headers is None:
        return []
    items = None
    if isinstance(headers, dict):
        items = headers.items()
    elif isinstance(headers, (list, tuple)):
        items = headers
    elif hasattr(headers, "items"):
        try:
            items = headers.items()
        except Exception:
            return []
    if items is None:
        return []

    pairs = []
    try:
        iterator = iter(items)
    except TypeError:
        return []
    for item in iterator:
        if isinstance(item, str):
            # 有些客户端把原始 headers 块直接塞进来，没有结构化就跳过。
            continue
        try:
            key, value = item
        except (TypeError, ValueError):
            continue
        pairs.append((str(key), value))
    return pairs


def _headers(response):
    raw = _lookup(response, ("headers",))
    normalized = {}
    for key, value in _header_items(raw):
        name = key.strip().lower()
        if not name:
            continue
        if isinstance(value, (list, tuple)):
            text = ", ".join(str(part) for part in value)
        else:
            text = str(value)
        if name in normalized:
            normalized[name] = normalized[name] + ", " + text
        else:
            normalized[name] = text
    return normalized


def _select_headers(headers):
    selected = {}
    for name in sorted(headers):
        if name in _SENSITIVE_HEADERS:
            continue
        selected[name] = headers[name][:_MAX_HEADER_VALUE]
    return selected


def _url(response):
    value = _lookup(response, ("url",))
    if value is None:
        request = _lookup(response, ("request",))
        if request is not None:
            value = _lookup(request, ("url",))
    if value is None:
        return None
    return str(value)[:_MAX_URL]


def _message(error):
    if error is None:
        return ""
    if isinstance(error, BaseException):
        text = str(error).strip()
        if not text:
            text = type(error).__name__
        return text[:_MAX_MESSAGE]
    return str(error).strip()[:_MAX_MESSAGE]


def _error_type(error):
    if isinstance(error, BaseException):
        return type(error).__name__
    return None


def _is_timeout_error(error, error_type):
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    name = (error_type or "").lower()
    return "timeout" in name


def _retry_after(headers):
    value = headers.get("retry-after")
    if not value:
        return None
    text = value.strip()
    try:
        seconds = int(float(text))
    except (TypeError, ValueError):
        pass
    else:
        return seconds if seconds >= 0 else None

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((when - datetime.now(timezone.utc)).total_seconds()))


def _blocked_status_signal(status, headers):
    if status in _BLOCKED_STATUS:
        return True
    if status not in _GATEWAY_STATUS:
        return False
    if "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0":
        return True
    if headers.get("cf-mitigated", "").lower() in {"challenge", "block", "managed"}:
        return True
    return "cloudflare" in headers.get("server", "").lower()


def _reason_for(status, headers, message, error, error_type):
    # 状态码优先，避免错误原文里的 timeout 字样覆盖 404 等确定结果。
    if status in _NOT_FOUND_STATUS:
        return NOT_FOUND
    if status in _BLOCKED_STATUS:
        return BLOCKED
    if status in _TIMEOUT_STATUS:
        return TIMEOUT
    if _is_timeout_error(error, error_type):
        return TIMEOUT
    if _blocked_status_signal(status, headers):
        return BLOCKED
    if headers.get("x-ratelimit-remaining") == "0":
        return BLOCKED
    if _NOT_FOUND_PATTERN.search(message):
        return NOT_FOUND
    if _BLOCKED_PATTERN.search(message):
        return BLOCKED
    if _TIMEOUT_PATTERN.search(message):
        return TIMEOUT
    return ERROR


def classify(error, response=None):
    """把一次失败归一成 ``reason``，并附上当时的证据。

    ``error`` 可以是异常、错误字符串或任意对象；``response`` 可以是
    requests 的 Response、带 ``status_code``/``headers``/``url`` 的对象，
    也可以是 ``{"status": 404, "headers": {...}, "url": "..."}`` 这样的字典。
    未显式传入 ``response`` 时，会尝试从异常的 ``response`` 属性取
    （例如 ``requests.HTTPError``）。
    """

    if response is None and not isinstance(error, (str, bytes)):
        response = _lookup(error, ("response",))
    status = _status_code(response)
    headers = _headers(response)
    message = _message(error)
    error_type = _error_type(error)
    reason = _reason_for(status, headers, message, error, error_type)

    evidence = {
        "reason": reason,
        "status": status,
        "headers": _select_headers(headers),
        "url": _url(response),
        "message": message,
        "error_type": error_type,
        "retry_after": _retry_after(headers),
    }
    return {"reason": reason, "evidence": evidence}


def policy(reason):
    """返回某类失败的基础处理策略（预算始终按站点共享）。"""

    if isinstance(reason, dict):
        reason = reason.get("reason", ERROR)
    return dict(_POLICIES.get(reason, _POLICIES[ERROR]))


def site_of(url):
    """从 URL 提取归一化站点名：小写、去端口、去 ``www.``。"""

    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    try:
        parts = urlsplit(text if "://" in text else "//" + text)
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None
