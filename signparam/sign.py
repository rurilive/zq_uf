"""配置驱动的接口签名参数生成器（纯标准库）。

规则（rule）声明参数排序、编码、时间戳/随机串格式和拼接方式，
接口保持 sign(params, rule)。返回签名、拼接串和参与签名的参数快照，
快照可用于规则改动后的回放验证。
"""
import codecs
import hashlib
import hmac
import re
import secrets
import string
import time
from datetime import datetime, timezone
from urllib.parse import quote as url_quote


class SignError(ValueError):
    """签名规则或参数不合法时抛出，绝不静默跳过。"""


_HASH_ALGOS = ("md5", "sha1", "sha256")
_HMAC_PREFIX = "hmac-"
_TIMESTAMP_FORMATS = ("epoch", "epoch_ms", "iso8601")
_NONCE_ALPHABET = string.ascii_letters + string.digits
_NONCE_MAX_LENGTH = 64

DEFAULT_RULE = {
    "sort": "asc",        # asc / desc / none（保持传入顺序）
    "encoding": "utf-8",  # 拼接串和密钥的编码
    "quote": False,       # 是否对键和值做 URL 百分号编码
    "kv_sep": "=",        # 键值连接符
    "pair_sep": "&",      # 参数对连接符
    "algo": "md5",        # md5 / sha1 / sha256 / hmac-md5 / hmac-sha1 / hmac-sha256
    "secret": "",         # HMAC 密钥，非 HMAC 算法必须留空
    "timestamp": None,    # 例如 {"param": "ts", "format": "epoch"}
    "nonce": None,        # 例如 {"param": "nonce", "length": 16}
}

_EPOCH_RE = re.compile(r"^\d{10}$")
_EPOCH_MS_RE = re.compile(r"^\d{13}$")
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9]+$")


def sign(params, rule=None):
    """按规则对参数签名。

    返回 {"sign": 签名十六进制串, "raw": 参与签名的拼接串,
    "snapshot": 参与签名的参数快照（含自动生成的时间戳/随机串）}。
    用同一规则对 snapshot 再次签名可回放验证。
    """
    cfg = _build_config(rule)
    snapshot = _prepare_params(params, cfg)
    raw = _join(snapshot, cfg)
    return {"sign": _digest(raw, cfg), "raw": raw, "snapshot": snapshot}


def _build_config(rule):
    if rule is None:
        rule = {}
    if not isinstance(rule, dict):
        raise SignError("rule 必须是 dict，收到 %s" % type(rule).__name__)
    unknown = sorted(set(rule) - set(DEFAULT_RULE))
    if unknown:
        raise SignError("未知规则字段: %s" % ", ".join(unknown))
    cfg = dict(DEFAULT_RULE)
    cfg.update(rule)

    if cfg["sort"] not in ("asc", "desc", "none"):
        raise SignError("sort 必须是 asc/desc/none，收到 %r" % (cfg["sort"],))
    if not isinstance(cfg["quote"], bool):
        raise SignError("quote 必须是 bool，收到 %r" % (cfg["quote"],))
    for field in ("kv_sep", "pair_sep"):
        if not isinstance(cfg[field], str) or not cfg[field]:
            raise SignError("%s 必须是非空字符串" % field)
    if not isinstance(cfg["encoding"], str):
        raise SignError("encoding 必须是字符串")
    try:
        codecs.lookup(cfg["encoding"])
    except LookupError:
        raise SignError("未知编码: %r" % (cfg["encoding"],)) from None

    algo = cfg["algo"]
    if not isinstance(algo, str):
        raise SignError("algo 必须是字符串")
    secret = cfg["secret"]
    if not isinstance(secret, str):
        raise SignError("secret 必须是字符串")
    if algo.startswith(_HMAC_PREFIX):
        if algo[len(_HMAC_PREFIX):] not in _HASH_ALGOS:
            raise SignError("不支持的 HMAC 算法: %r" % algo)
        if not secret:
            raise SignError("HMAC 算法必须配置非空 secret")
    else:
        if algo not in _HASH_ALGOS:
            raise SignError("不支持的算法: %r" % (algo,))
        if secret:
            raise SignError("非 HMAC 算法不应配置 secret")

    cfg["timestamp"] = _check_time_spec(cfg["timestamp"])
    cfg["nonce"] = _check_nonce_spec(cfg["nonce"])
    return cfg


def _check_time_spec(spec):
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) != {"param", "format"}:
        raise SignError('timestamp 必须是 {"param": ..., "format": ...}')
    if not isinstance(spec["param"], str) or not spec["param"]:
        raise SignError("timestamp.param 必须是非空字符串")
    if spec["format"] not in _TIMESTAMP_FORMATS:
        raise SignError("timestamp.format 必须是 %s 之一" % "/".join(_TIMESTAMP_FORMATS))
    return dict(spec)


def _check_nonce_spec(spec):
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) != {"param", "length"}:
        raise SignError('nonce 必须是 {"param": ..., "length": ...}')
    if not isinstance(spec["param"], str) or not spec["param"]:
        raise SignError("nonce.param 必须是非空字符串")
    length = spec["length"]
    if not isinstance(length, int) or isinstance(length, bool) \
            or not 1 <= length <= _NONCE_MAX_LENGTH:
        raise SignError("nonce.length 必须是 1..%d 的整数" % _NONCE_MAX_LENGTH)
    return dict(spec)


def _prepare_params(params, cfg):
    if not isinstance(params, dict):
        raise SignError("params 必须是 dict，收到 %s" % type(params).__name__)
    if not params:
        raise SignError("params 不能为空")
    snapshot = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key:
            raise SignError("参数名必须是非空字符串: %r" % (key,))
        snapshot[key] = _stringify(key, value)
    _apply_timestamp(snapshot, cfg)
    _apply_nonce(snapshot, cfg)
    return snapshot


def _stringify(key, value):
    if isinstance(value, str):
        if not value:
            raise SignError("参数 %r 的值不能为空字符串" % key)
        return value
    if isinstance(value, bool):
        raise SignError("参数 %r 是布尔值，请显式转成字符串" % key)
    if isinstance(value, (int, float)):
        return str(value)
    raise SignError("参数 %r 的值类型不支持: %s" % (key, type(value).__name__))


def _apply_timestamp(snapshot, cfg):
    spec = cfg["timestamp"]
    if spec is None:
        return
    name, fmt = spec["param"], spec["format"]
    if name in snapshot:
        _validate_timestamp(name, snapshot[name], fmt)
        return
    now = time.time()
    if fmt == "epoch":
        snapshot[name] = str(int(now))
    elif fmt == "epoch_ms":
        snapshot[name] = str(int(now * 1000))
    else:
        snapshot[name] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_timestamp(name, value, fmt):
    ok = False
    if fmt == "epoch":
        ok = bool(_EPOCH_RE.match(value))
    elif fmt == "epoch_ms":
        ok = bool(_EPOCH_MS_RE.match(value))
    elif fmt == "iso8601":
        ok = bool(_ISO8601_RE.match(value))
        if ok:
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                ok = False
    if not ok:
        raise SignError("参数 %r 的值 %r 不符合时间戳格式 %s" % (name, value, fmt))


def _apply_nonce(snapshot, cfg):
    spec = cfg["nonce"]
    if spec is None:
        return
    name, length = spec["param"], spec["length"]
    if name in snapshot:
        value = snapshot[name]
        if len(value) != length or not _NONCE_RE.match(value):
            raise SignError(
                "参数 %r 的值 %r 不是 %d 位字母数字随机串" % (name, value, length))
        return
    snapshot[name] = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(length))


def _join(snapshot, cfg):
    keys = list(snapshot)
    if cfg["sort"] == "asc":
        keys.sort()
    elif cfg["sort"] == "desc":
        keys.sort(reverse=True)
    parts = []
    for key in keys:
        k = _maybe_quote(key, cfg)
        v = _maybe_quote(snapshot[key], cfg)
        parts.append(k + cfg["kv_sep"] + v)
    return cfg["pair_sep"].join(parts)


def _maybe_quote(text_value, cfg):
    if not cfg["quote"]:
        return text_value
    return url_quote(text_value, safe="", encoding=cfg["encoding"])


def _digest(raw, cfg):
    data = raw.encode(cfg["encoding"])
    algo = cfg["algo"]
    if algo.startswith(_HMAC_PREFIX):
        return hmac.new(
            cfg["secret"].encode(cfg["encoding"]), data, algo[len(_HMAC_PREFIX):]
        ).hexdigest()
    return hashlib.new(algo, data).hexdigest()
