"""采集流量脱敏录制与确定性回放。"""
import glob
import json
import os
import re

DEFAULT_PATH = "traffic.jsonl"
DEFAULT_BATCH = "default"
MASK_CHAR = "*"

COOKIE_KEEP_PREFIX = 2
COOKIE_KEEP_SUFFIX = 2
TOKEN_KEEP_PREFIX = 2
TOKEN_KEEP_SUFFIX = 2
PHONE_KEEP_PREFIX = 3
PHONE_KEEP_SUFFIX = 2

PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
COOKIE_HEADERS = {"cookie"}
TOKEN_HEADERS = {"authorization", "x-token", "token", "x-access-token"}
TOKEN_KEYS = {"token", "access_token", "authorization"}


def _mask_value(text, keep_prefix, keep_suffix):
    """掩码中段，保留长度与前后缀；过短的值整体打码。"""
    text = str(text)
    if len(text) <= keep_prefix + keep_suffix:
        return MASK_CHAR * len(text)
    middle = len(text) - keep_prefix - keep_suffix
    suffix = text[-keep_suffix:] if keep_suffix else ""
    return text[:keep_prefix] + MASK_CHAR * middle + suffix


def _mask_cookie(header_value):
    parts = []
    for item in str(header_value).split(";"):
        item = item.strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        if not sep:
            parts.append(item)
            continue
        parts.append("{}={}".format(
            name.strip(),
            _mask_value(value.strip(), COOKIE_KEEP_PREFIX, COOKIE_KEEP_SUFFIX),
        ))
    return "; ".join(parts)


def _mask_token(value):
    text = str(value)
    scheme, sep, rest = text.partition(" ")
    if sep and rest.strip():
        return "{} {}".format(
            scheme, _mask_value(rest.strip(), TOKEN_KEEP_PREFIX, TOKEN_KEEP_SUFFIX)
        )
    return _mask_value(text, TOKEN_KEEP_PREFIX, TOKEN_KEEP_SUFFIX)


def _mask_phones(text):
    return PHONE_PATTERN.sub(
        lambda match: _mask_value(match.group(0), PHONE_KEEP_PREFIX, PHONE_KEEP_SUFFIX),
        text,
    )


def _mask_body(value):
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            if str(key).lower() in TOKEN_KEYS and isinstance(item, str):
                masked[key] = _mask_token(item)
            else:
                masked[key] = _mask_body(item)
        return masked
    if isinstance(value, list):
        return [_mask_body(item) for item in value]
    if isinstance(value, str):
        return _mask_phones(value)
    return value


def mask_request(request):
    """返回脱敏后的请求副本，原对象不被修改。"""
    if not isinstance(request, dict):
        raise TypeError(
            "request 必须是 dict，收到 {}".format(type(request).__name__)
        )
    masked = dict(request)
    headers = request.get("headers")
    if isinstance(headers, dict):
        new_headers = {}
        for name, value in headers.items():
            lower = str(name).lower()
            if lower in COOKIE_HEADERS:
                new_headers[name] = _mask_cookie(value)
            elif lower in TOKEN_HEADERS:
                new_headers[name] = _mask_token(value)
            elif isinstance(value, str):
                new_headers[name] = _mask_phones(value)
            else:
                new_headers[name] = value
        masked["headers"] = new_headers
    if isinstance(masked.get("url"), str):
        masked["url"] = _mask_phones(masked["url"])
    if "body" in masked:
        masked["body"] = _mask_body(masked["body"])
    return masked


def record(request, path=None, batch=DEFAULT_BATCH, mask=False):
    """把请求追加写入录制文件，每行一条，带批次号。

    mask=True 时按规则对 cookie、token、手机号脱敏后落盘。
    """
    if not isinstance(request, dict):
        raise TypeError(
            "request 必须是 dict，收到 {}".format(type(request).__name__)
        )
    target = path or DEFAULT_PATH
    stored = mask_request(request) if mask else dict(request)
    entry = {"batch": batch, "masked": bool(mask), "request": stored}
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return os.path.abspath(target)


def _rebuild(request):
    """按原方法、头和体重建请求字典，其余字段原样保留。"""
    rebuilt = {}
    for key in ("method", "url", "headers", "body"):
        if key in request:
            rebuilt[key] = request[key]
    for key, value in request.items():
        rebuilt.setdefault(key, value)
    return rebuilt


def _unwrap(entry):
    if "request" in entry and isinstance(entry["request"], dict):
        return entry["request"]
    return entry


def _load_entries(path):
    entries = []
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "{}:{}: 录制行不是合法 JSON: {}".format(path, lineno, exc)
                ) from exc
            if not isinstance(entry, dict):
                raise ValueError(
                    "{}:{}: 录制行必须是 JSON 对象".format(path, lineno)
                )
            entries.append(entry)
    return entries


def _default_drill_path(path):
    root, ext = os.path.splitext(path)
    return root + ".drill" + (ext or ".jsonl")


def replay(path, drill_path=None):
    """读回录制文件并按原方法、头、体重建请求列表。

    重建结果同时写入独立演练库：drill_path 指定位置；
    缺省写到 <录制文件名>.drill.jsonl；drill_path=False 关闭。
    文件不存在时返回空列表；行损坏时抛出带行号的 ValueError。
    """
    if not os.path.exists(path):
        return []
    requests = [_rebuild(_unwrap(entry)) for entry in _load_entries(path)]
    if drill_path is False:
        target = None
    elif drill_path is None:
        target = _default_drill_path(path)
    else:
        target = drill_path
    if requests and target:
        with open(target, "a", encoding="utf-8") as handle:
            for req in requests:
                drill = {"source": os.path.abspath(path), "request": req}
                handle.write(json.dumps(drill, ensure_ascii=False) + "\n")
    return requests


def find(batch, paths):
    """跨录制文件按批次号检索，返回重建后的请求列表。

    paths 可以是单个路径、文件列表或目录（检索目录下 *.jsonl）。
    路径不存在时抛出 FileNotFoundError，不静默跳过。
    """
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    files = []
    for item in paths:
        item = os.fspath(item)
        if os.path.isdir(item):
            files.extend(sorted(glob.glob(os.path.join(item, "*.jsonl"))))
        elif os.path.exists(item):
            files.append(item)
        else:
            raise FileNotFoundError("录制文件不存在: {}".format(item))
    matched = []
    for name in files:
        for entry in _load_entries(name):
            if entry.get("batch") == batch:
                matched.append(_rebuild(_unwrap(entry)))
    return matched

