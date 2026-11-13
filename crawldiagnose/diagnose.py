"""采集断点诊断：完整捕获、确定性重放、结构化对比。

capture(request) 记录方法、URL、头、体、响应状态、响应摘要和捕获时间；
request 里可带 "replay" 模拟重放时重新观测到的请求/响应。
diagnose(id) 用固定时钟和固定随机源重建请求，敏感字段按规则替换后
做结构化对比，输出分类、差异字段和可能的改版点。
"""

import hashlib
import random
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPLAY_TIME = 1700000000
REPLAY_SEED = 20240101

SENSITIVE_HEADERS = {"cookie", "authorization", "set-cookie", "x-token"}
TIME_PARAMS = {"timestamp", "ts", "_t", "_"}
NONCE_PARAMS = {"nonce", "noncestr", "rand"}
SIGN_PARAMS = {"sign", "signature", "sig", "token", "access_token"}

_captures = {}


def _digest(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _replace_param(key):
    """敏感参数按规则替换，只依赖参数名，保证重放确定性。"""
    name = key.lower()
    if name in TIME_PARAMS:
        return str(REPLAY_TIME)
    if name in NONCE_PARAMS:
        return str(random.Random("%s:%s" % (REPLAY_SEED, name)).getrandbits(32))
    if name in SIGN_PARAMS:
        return "replay-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return None


def _sanitize_params(pairs):
    out = []
    for key, value in sorted(pairs):
        replaced = _replace_param(key)
        out.append((key, replaced if replaced is not None else value))
    return out


def _sanitize_url(url):
    parts = urlsplit(url or "")
    query = urlencode(_sanitize_params(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _sanitize_headers(headers):
    return {
        key: ("<redacted>" if key.lower() in SENSITIVE_HEADERS else value)
        for key, value in (headers or {}).items()
    }


def _sanitize_body(body):
    if not body:
        return body
    text = body.lstrip()
    if "=" in body and not text.startswith(("{", "[")):
        pairs = parse_qsl(body, keep_blank_values=True)
        if pairs:
            return urlencode(_sanitize_params(pairs))
    return body


def _rebuild(request, defaults=None):
    """按重放规则重建请求：固定时间/随机源，敏感字段替换。"""
    merged = dict(defaults or {})
    merged.update(request)
    return {
        "method": merged.get("method"),
        "url": _sanitize_url(merged.get("url")),
        "headers": _sanitize_headers(merged.get("headers")),
        "body": _sanitize_body(merged.get("body")),
    }


def capture(request):
    """捕获一条断点：方法、头、体、状态、响应摘要和捕获时间。"""
    cid = str(len(_captures) + 1)
    _captures[cid] = {
        "id": cid,
        "method": request.get("method"),
        "url": request.get("url"),
        "headers": dict(request.get("headers") or {}),
        "body": request.get("body"),
        "status": request.get("status"),
        "response": request.get("response"),
        "response_digest": _digest(request.get("response")),
        "captured_at": request.get("timestamp", time.time()),
        "replay": request.get("replay"),
    }
    return cid


def _request_diff(original, replayed):
    diff = {}
    for field in ("method", "url", "body"):
        if original[field] != replayed[field]:
            diff[field] = {"capture": original[field], "replay": replayed[field]}
    header_diff = {}
    for key in sorted(set(original["headers"]) | set(replayed["headers"])):
        old = original["headers"].get(key)
        new = replayed["headers"].get(key)
        if old != new:
            header_diff[key] = {"capture": old, "replay": new}
    if header_diff:
        diff["headers"] = header_diff
    return diff


def _hints(request_diff, status_diff, response_diff):
    hints = []
    if "method" in request_diff:
        hints.append("接口方法可能调整")
    if "url" in request_diff:
        hints.append("接口路径或查询参数可能改版，检查签名/时间戳参数规则")
    if "headers" in request_diff:
        hints.append("请求头要求可能变化，检查是否新增校验头或登录态失效")
    if "body" in request_diff:
        hints.append("表单或请求体字段可能改版")
    if status_diff:
        hints.append("状态码 %s -> %s，可能触发风控、权限变化或接口下线"
                     % (status_diff["capture"], status_diff["replay"]))
    if response_diff:
        hints.append("页面结构或接口返回字段可能改版，建议对比响应摘要")
    return hints


def diagnose(cid):
    """重放并对比断点，返回分类、差异字段和可能的改版点。"""
    if cid not in _captures:
        raise KeyError(cid)
    cap = _captures[cid]
    replay_obs = cap.get("replay") or {}

    defaults = {
        "method": cap["method"],
        "url": cap["url"],
        "headers": cap["headers"],
        "body": cap["body"],
    }
    original = _rebuild(defaults)
    replayed = _rebuild(replay_obs.get("request") or {}, defaults)

    replay_status = replay_obs.get("status", cap["status"])
    replay_response = replay_obs.get("response", cap["response"])
    replay_digest = _digest(replay_response)

    request_diff = _request_diff(original, replayed)
    status_diff = None
    if replay_status != cap["status"]:
        status_diff = {"capture": cap["status"], "replay": replay_status}
    response_diff = None
    if replay_digest != cap["response_digest"]:
        response_diff = {"capture": cap["response_digest"], "replay": replay_digest}

    if request_diff:
        category = "request_changed"
    elif status_diff:
        category = "status_changed"
    elif response_diff:
        category = "response_changed"
    else:
        category = "consistent"

    return {
        "id": cid,
        "method": cap["method"],
        "url": cap["url"],
        "status": cap["status"],
        "response": cap["response"],
        "response_digest": cap["response_digest"],
        "captured_at": cap["captured_at"],
        "category": category,
        "request": original,
        "replay": {
            "request": replayed,
            "status": replay_status,
            "response_digest": replay_digest,
        },
        "request_diff": request_diff,
        "status_diff": status_diff,
        "response_diff": response_diff,
        "hints": _hints(request_diff, status_diff, response_diff),
    }
