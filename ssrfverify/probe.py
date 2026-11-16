"""基于回连证据的 SSRF 验证。

``check(url, param)`` 生成带唯一标识的回连 payload，注入参数后请求目标；
只有接收端收到该标识的回连才判定为命中。没有回连但响应异常记为可疑，
并保留原始响应。接收端不可用时抛 :class:`ReceiverUnavailable` 并停止本轮，
不会把「没有回连」当成安全结论。

仅用于已获授权的安全测试。
"""

from __future__ import annotations

import copy
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .errors import ReceiverUnavailable
from .receiver import BaseReceiver, Callback, EmbeddedReceiver

__all__ = [
    "HttpResponse",
    "check",
    "report",
    "reset",
    "configure",
    "callbacks",
    "shutdown",
]

DEFAULT_TIMEOUT = 5.0
_MAX_BODY = 256 * 1024
USER_AGENT = "ssrfverify/0.2 (authorized testing)"

# 只用于把「没有回连」的结果降级为可疑，不用于判定命中。
_INDICATORS = (
    "root:x:0:0",
    "uid=",
    "169.254.169.254",
    "metadata.google.internal",
    "ami-id",
    "iam/security-credentials",
    "x-amz-security-token",
    "begin rsa private key",
    "begin openssh private key",
    "/etc/passwd",
)

_UNSET = object()


@dataclass
class HttpResponse:
    """目标返回的原始响应。"""

    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    truncated: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "headers": dict(self.headers),
            "body": self.body,
            "truncated": self.truncated,
        }


class _Round:
    def __init__(self) -> None:
        self.records: Dict[Tuple[str, str], Dict[str, object]] = {}
        self.order: List[Tuple[str, str]] = []
        self.checks = 0
        self.tokens: set = set()
        self.halted: Optional[str] = None
        self.receivers: List[BaseReceiver] = []

    def use_receiver(self, receiver: BaseReceiver) -> None:
        if receiver not in self.receivers:
            self.receivers.append(receiver)


_LOCK = threading.RLock()
_round = _Round()

_receiver: Optional[BaseReceiver] = None
_owned_receiver: Optional[EmbeddedReceiver] = None
_known_receivers: List[BaseReceiver] = []
_timeout = DEFAULT_TIMEOUT
_transport: Optional[Callable[[str, float], HttpResponse]] = None


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def configure(
    *,
    receiver: Optional[BaseReceiver] = _UNSET,  # type: ignore[assignment]
    timeout: Optional[float] = None,
    transport: Optional[Callable[[str, float], HttpResponse]] = _UNSET,  # type: ignore[assignment]
) -> None:
    """更换接收端 / 超时 / 请求实现，并开始新一轮。

    ``receiver=None`` 表示回到内嵌接收端。``transport`` 接收 ``(url, timeout)``，
    返回 :class:`HttpResponse`，抛异常表示请求失败。
    """
    global _receiver, _owned_receiver, _timeout, _transport
    with _LOCK:
        if timeout is not None:
            if timeout < 0:
                raise ValueError("timeout must be >= 0")
            _timeout = float(timeout)
        if transport is not _UNSET:
            _transport = transport
        if receiver is not _UNSET:
            _receiver = receiver
            if _owned_receiver is not None and _owned_receiver is not receiver:
                _owned_receiver.stop()
                _owned_receiver = None
        reset()


def _get_receiver(explicit: Optional[BaseReceiver]) -> BaseReceiver:
    global _receiver, _owned_receiver
    if explicit is not None:
        return explicit
    with _LOCK:
        if _receiver is None:
            receiver = EmbeddedReceiver()
            receiver.start()  # 失败时抛 ReceiverUnavailable
            _receiver = receiver
            _owned_receiver = receiver
        return _receiver


def _remember_receiver(receiver: BaseReceiver) -> None:
    """记住见过的接收端，``callbacks()`` 的审计记录跨轮保留。"""
    with _LOCK:
        if receiver not in _known_receivers:
            _known_receivers.append(receiver)


def shutdown() -> None:
    """停止内嵌接收端并丢弃接收端索引（接收端自身已记录的审计随之释放）。"""
    global _receiver, _owned_receiver
    with _LOCK:
        if _owned_receiver is not None:
            _owned_receiver.stop()
        if _receiver is _owned_receiver:
            _receiver = None
        _owned_receiver = None
        _known_receivers.clear()


def _halt(message: str) -> None:
    with _LOCK:
        if _round.halted is None:
            _round.halted = message


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def check(
    url: str,
    param: str,
    *,
    receiver: Optional[BaseReceiver] = None,
    timeout: Optional[float] = None,
    transport: Optional[Callable[[str, float], HttpResponse]] = None,
    force: bool = False,
) -> Dict[str, object]:
    """验证 ``url`` 的 ``param`` 参数是否可被用于 SSRF。

    同一 ``(url, param)`` 默认不重复探测：再次调用返回上一轮结论，
    避免报告里堆积重复项；需要重试时传 ``force=True``。
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required")
    if not isinstance(param, str) or not param.strip():
        raise ValueError("param is required")
    url, param = url.strip(), param.strip()

    with _LOCK:
        if _round.halted:
            raise ReceiverUnavailable(_round.halted)
        try:
            recv = _get_receiver(receiver)
        except ReceiverUnavailable as exc:
            _halt(str(exc))
            raise
        if not recv.available():
            message = f"callback receiver unavailable: {type(recv).__name__}"
            _halt(message)
            raise ReceiverUnavailable(message)
        if not recv.base_url:
            message = "callback receiver has no reachable base_url"
            _halt(message)
            raise ReceiverUnavailable(message)

        _round.checks += 1
        key = (url, param)
        previous = _round.records.get(key)
        if previous is not None and not force:
            previous["attempts"] = int(previous["attempts"]) + 1
            result = copy.deepcopy(previous)
            result["reused"] = True
            return result

        wait_timeout = _timeout if timeout is None else float(timeout)
        sender = transport or _transport or _http_get
        token = secrets.token_hex(16)
        _round.tokens.add(token)
        _round.use_receiver(recv)
        _remember_receiver(recv)

    payload = recv.base_url.rstrip("/") + "/c/" + token
    target = build_probe_url(url, param, payload)
    started = time.time()

    response: Optional[HttpResponse] = None
    error: Optional[str] = None
    try:
        response = _normalize_response(sender(target, wait_timeout))
    except Exception as exc:  # 请求失败仍等待回连，目标可能已经处理过
        error = f"{type(exc).__name__}: {exc}"

    callback = recv.wait(token, wait_timeout)

    if callback is not None:
        status, reason = "hit", "callback_received"
    elif error is not None:
        status, reason = "suspicious", "transport_error"
    else:
        reason = _abnormal_reason(response, payload)
        status = "suspicious" if reason else "clean"

    callback_info = callback.to_dict() if callback is not None else None
    record = {
        "id": token,
        "token": token,
        "url": url,
        "param": param,
        "target": target,
        "payload": payload,
        "status": status,
        "hit": status == "hit",
        "reason": reason,
        "at": started,
        "callback_at": callback_info["at"] if callback_info else None,
        "callback_elapsed": (callback_info["at"] - started) if callback_info else None,
        "callback": callback_info,
        "source_address": callback_info["source_address"] if callback_info else None,
        "forwarded": callback_info["forwarded"] if callback_info else False,
        "via": callback_info["via"] if callback_info else None,
        "response": response.to_dict() if response is not None else None,
        "error": error,
        "attempts": 1,
    }

    with _LOCK:
        _round.records[(url, param)] = record
        if (url, param) not in _round.order:
            _round.order.append((url, param))

    result = copy.deepcopy(record)
    result["reused"] = False
    return result


def report() -> Dict[str, object]:
    """按接口汇总本轮结论；每条命中都带可回查的回连标识。"""
    with _LOCK:
        items: List[Dict[str, object]] = []
        for key in _round.order:
            record = _round.records[key]
            items.append(
                {
                    "url": record["url"],
                    "param": record["param"],
                    "target": record["target"],
                    "status": record["status"],
                    "id": record["id"],
                    "token": record["token"],
                    "reason": record["reason"],
                    "at": record["at"],
                    "callback_at": record["callback_at"],
                    "callback_elapsed": record["callback_elapsed"],
                    "source_address": record["source_address"],
                    "forwarded": record["forwarded"],
                    "via": record["via"],
                    "attempts": record["attempts"],
                    "payload": record["payload"],
                }
            )
        hits = [item for item in items if item["status"] == "hit"]
        suspicious = [
            copy.deepcopy(_round.records[key])
            for key in _round.order
            if _round.records[key]["status"] == "suspicious"
        ]
        clean = [item for item in items if item["status"] == "clean"]

        receivers = list(_round.receivers)
        all_callbacks = []
        for receiver in receivers:
            all_callbacks.extend(receiver.callbacks())
        matched = [
            callback.to_dict()
            for callback in all_callbacks
            if callback.token in _round.tokens
        ]
        matched.sort(key=lambda item: item["at"])
        primary = receivers[0] if receivers else None
        return {
            "round_status": "aborted" if _round.halted else "ok",
            "error": _round.halted,
            "receiver": {
                "base_url": primary.base_url if primary is not None else "",
                "available": all(r.available() for r in receivers) if receivers else False,
                "records": len(all_callbacks),
            },
            "checks": _round.checks,
            "pairs": len(_round.order),
            "duplicates": _round.checks - len(_round.order),
            "hits": hits,
            "suspicious": suspicious,
            "clean": clean,
            "items": items,
            "callbacks": matched,
        }


def callbacks() -> List[Dict[str, object]]:
    """接收端保留的全部回连记录（含来源地址与请求头），跨轮保留。"""
    records: List[Callback] = []
    seen = set()
    with _LOCK:
        receivers = list(_known_receivers)
    for receiver in receivers:
        for callback in receiver.callbacks():
            key = (callback.token, callback.at, callback.source_address)
            if key in seen:
                continue
            seen.add(key)
            records.append(callback)
    records.sort(key=lambda item: item.at)
    return [callback.to_dict() for callback in records]


def reset() -> Dict[str, object]:
    """清空本轮记录（接收端的审计记录保留）。"""
    with _LOCK:
        _round.records.clear()
        _round.order.clear()
        _round.tokens.clear()
        _round.receivers.clear()
        _round.checks = 0
        _round.halted = None
        return {"checks": 0}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def build_probe_url(url: str, param: str, payload: str) -> str:
    """把 ``payload`` 注入 ``url`` 的 ``param`` 参数。"""
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    query = []
    for key, value in pairs:
        if key == param:
            query.append((key, payload))
            replaced = True
        else:
            query.append((key, value))
    if not replaced:
        query.append((param, payload))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment)
    )


def _http_get(url: str, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _read_response(response.status, response.headers, response)
    except urllib.error.HTTPError as exc:
        return _read_response(exc.code, exc.headers, exc)


def _read_response(status: int, headers, stream) -> HttpResponse:
    body = stream.read(_MAX_BODY + 1)
    truncated = len(body) > _MAX_BODY
    if truncated:
        body = body[:_MAX_BODY]
    return HttpResponse(
        status=int(status),
        headers={str(key): str(value) for key, value in (headers or {}).items()},
        body=body.decode("utf-8", errors="replace"),
        truncated=truncated,
    )


def _normalize_response(response) -> HttpResponse:
    if response is None:
        raise ValueError("transport returned no response")
    if isinstance(response, HttpResponse):
        return response
    if isinstance(response, dict):
        return HttpResponse(
            status=int(response.get("status", 0)),
            headers={str(k): str(v) for k, v in (response.get("headers") or {}).items()},
            body=str(response.get("body", "")),
            truncated=bool(response.get("truncated", False)),
        )
    if isinstance(response, tuple):
        status, headers, body = response
        raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
        return HttpResponse(
            status=int(status),
            headers={str(k): str(v) for k, v in (headers or {}).items()},
            body=raw.decode("utf-8", errors="replace"),
        )
    raise TypeError(f"unsupported transport response: {type(response).__name__}")


def _abnormal_reason(response: Optional[HttpResponse], payload: str) -> Optional[str]:
    if response is None:
        return "no_response"
    body = response.body or ""
    lowered = body.lower()
    if payload in body or quote(payload, safe="") in body:
        return "payload_echoed"
    host = urlsplit(payload).netloc
    if host and host.lower() in lowered:
        return "callback_host_echoed"
    for marker in _INDICATORS:
        if marker in lowered:
            return f"internal_marker:{marker}"
    if response.status >= 500:
        return "server_error"
    return None
