"""URL 归一化与指纹：去掉无意义跟踪参数、统一 query 顺序和编码后再去重。"""
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

DEFAULT_TRACKING_PARAMS = frozenset({
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "spm", "ref", "ref_src", "source", "_ga", "_gl",
    "yclid", "wickedid", "zanpid",
})
DEFAULT_TRACKING_PREFIXES = ("utm_", "pk_", "piwik_", "matomo_")

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class NormalizeConfig:
    """归一化规则，全部可配置。

    drop_params:     按完整参数名（小写）丢弃，默认常见跟踪参数。
    drop_prefixes:   按参数名前缀（小写）丢弃，默认 utm_ 等。
    keep_params:     若设置则为白名单，只有名单内的参数保留（优先级最高）。
    sort_query:      query 键值对按 (key, value) 排序。
    strip_fragment:  去掉 #fragment。
    strip_default_port: 去掉 80/443 默认端口。
    strip_trailing_slash: 去掉路径末尾的 /（根路径除外）。
    """
    drop_params: frozenset = DEFAULT_TRACKING_PARAMS
    drop_prefixes: tuple = DEFAULT_TRACKING_PREFIXES
    keep_params: Optional[frozenset] = None
    sort_query: bool = True
    strip_fragment: bool = True
    strip_default_port: bool = True
    strip_trailing_slash: bool = False


def normalize_url(url, config=NormalizeConfig()):
    """返回归一化后的 URL 字符串；同一篇文章的变体应得到相同结果。"""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if config.strip_default_port and port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host if port is None else "%s:%d" % (host, port)

    # 统一百分号编码：先解码再按统一 safe 集编码，%7E/~、+/%20 等等价
    path = quote(unquote(parts.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    if config.strip_trailing_slash and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low = key.lower()
        if config.keep_params is not None and low not in config.keep_params:
            continue
        if low in config.drop_params:
            continue
        if any(low.startswith(p) for p in config.drop_prefixes):
            continue
        kept.append((key, value))
    if config.sort_query:
        kept.sort()
    query = urlencode(kept, quote_via=quote)

    fragment = "" if config.strip_fragment else parts.fragment
    return urlunsplit((scheme, netloc, path, query, fragment))


def fingerprint(url, config=NormalizeConfig()):
    """归一化后的 SHA-256，作为去重主键。"""
    return sha256(normalize_url(url, config).encode("utf-8")).hexdigest()
