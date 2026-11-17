"""SSRF 回连验证：命中以接收端收到回连为准，不再按响应文本猜。"""

import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

_CHECKS = []
_RECEIVER = None
_ABORTED = False

WAIT_SECONDS = 2.0
FETCH_TIMEOUT = 5.0


class ReceiverUnavailable(RuntimeError):
    """接收端不可用，本轮检测已停止，不把没有回连的结果当成安全。"""


def start_receiver(host="127.0.0.1", port=0):
    """启动（或复用）回连接收端，返回接收端实例。"""
    global _RECEIVER
    if _RECEIVER is None or not _RECEIVER.alive():
        from .receiver import CallbackReceiver

        _RECEIVER = CallbackReceiver(host, port).start()
    return _RECEIVER


def attach_receiver(receiver):
    """挂接外部接收端实例。"""
    global _RECEIVER
    _RECEIVER = receiver
    return _RECEIVER


def stop_receiver():
    global _RECEIVER
    if _RECEIVER is not None:
        _RECEIVER.stop()
        _RECEIVER = None


def reset():
    """清空本轮记录并解除停止状态，接收端日志一并清掉。"""
    global _ABORTED
    _CHECKS.clear()
    _ABORTED = False
    if _RECEIVER is not None:
        _RECEIVER.clear()
    return {"checks": 0}


def _require_receiver():
    global _ABORTED
    if _ABORTED:
        raise ReceiverUnavailable("round already stopped: receiver unavailable")
    if _RECEIVER is None or not _RECEIVER.alive():
        _ABORTED = True
        raise ReceiverUnavailable("callback receiver unavailable, round stopped")


def _build_probe_url(url, param, payload):
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    new_query = []
    for key, value in query:
        if key == param and not replaced:
            new_query.append((key, payload))
            replaced = True
        else:
            new_query.append((key, value))
    if not replaced:
        new_query.append((param, payload))
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(new_query))
    )


def _fetch(url):
    """返回 (status, body, error)；网络异常时 status 为 None。"""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return exc.code, body, None
    except (urllib.error.URLError, OSError) as exc:
        return None, "", str(exc)


def _is_direct(target_url, source):
    """回连来源是否就是目标主机本身（解析失败时返回 None，交给人工判断）。"""
    host = urllib.parse.urlsplit(target_url).hostname
    try:
        return source == socket.gethostbyname(host)
    except (OSError, TypeError):
        return None


def check(url, param):
    """对 url 的 param 注入带回连 token 的 payload，收到回连才算命中。

    没收到回连但响应异常（网络错误或 5xx）记为可疑并保留原始响应。
    接收端不可用时抛出 ReceiverUnavailable 并停止本轮。
    """
    if not isinstance(url, str) or not url:
        raise ValueError("url is required")
    if not isinstance(param, str) or not param:
        raise ValueError("param is required")
    _require_receiver()

    token = uuid.uuid4().hex[:12]
    payload = "{}/cb/{}".format(_RECEIVER.base_url, token)
    probe_url = _build_probe_url(url, param, payload)
    status, body, error = _fetch(probe_url)
    callback = _RECEIVER.wait_for(token, WAIT_SECONDS)

    hit = callback is not None
    suspicious = not hit and (error is not None or (status is not None and status >= 500))
    record = {
        "url": url,
        "param": param,
        "token": token,
        "hit": hit,
        "callback_at": callback["at"] if callback else None,
        "source": callback["source"] if callback else None,
        "headers": callback["headers"] if callback else None,
        "direct": _is_direct(url, callback["source"]) if callback else None,
        "suspicious": suspicious,
        "reason": ("request error: " + error) if error else (
            "http {}".format(status) if suspicious else None
        ),
        "response": body.strip()[:200] if suspicious else None,
        "at": time.time(),
    }
    _CHECKS.append(record)
    return dict(record)


def report():
    """汇总本轮结论：命中去重，可疑项按接口分组，回连记录与命中逐条对应。"""
    hits = []
    callbacks = []
    seen = set()
    for item in _CHECKS:
        key = (item["url"], item["param"])
        if item["hit"] and key not in seen:
            seen.add(key)
            hits.append({
                "token": item["token"],
                "url": item["url"],
                "param": item["param"],
                "callback_at": item["callback_at"],
                "source": item["source"],
                "direct": item["direct"],
            })
            callbacks.append({
                "token": item["token"],
                "source": item["source"],
                "headers": item["headers"],
                "at": item["callback_at"],
            })
    suspicious = {}
    for item in _CHECKS:
        if item["suspicious"]:
            suspicious.setdefault(item["url"], []).append({
                "param": item["param"],
                "reason": item["reason"],
                "response": item["response"],
            })
    pairs = {(item["url"], item["param"]) for item in _CHECKS}
    return {
        "checks": len(_CHECKS),
        "hits": hits,
        "suspicious": suspicious,
        "callbacks": callbacks,
        "duplicates": len(_CHECKS) - len(pairs),
        "aborted": _ABORTED,
    }
