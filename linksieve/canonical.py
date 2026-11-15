"""URL 规范化：生成用于判重的内容键，并记录被丢弃的成分。"""
from urllib.parse import parse_qsl, urlencode, urlsplit

from .rules import DEFAULT_RULESET


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def canonicalize(url, rules=None):
    """返回 (内容键, 明细)。

    内容键保留路径、分页参数与有业务含义的查询参数，去掉追踪参数、
    参数顺序和页尾锚点。明细只用于解释判定，不参与指纹。
    """
    rules = rules or DEFAULT_RULESET
    raw = (url or "").strip()
    detail = {
        "input": raw,
        "ruleset": rules.version,
        "removed_params": [],
        "fragment_removed": False,
        "query_sorted": False,
        "path_rules": [],
        "host_lowercased": False,
        "default_port_removed": False,
        "preserved_pagination": [],
    }
    if not raw:
        return "", detail

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = _normalize_netloc(parts.netloc, scheme, detail)
    path = _normalize_path(parts.path, rules, detail)
    fragment = parts.fragment

    if rules.drop_fragment and fragment:
        detail["fragment_removed"] = True
        fragment = ""

    query = _normalize_query(parts.query, rules, detail)

    key = parts._replace(
        scheme=scheme, netloc=netloc, path=path, query=query, fragment=fragment
    ).geturl()
    return key, detail


def rule_hits(detail):
    """把规范化明细翻译成可对外展示的规则命中列表。"""
    hits = [f"tracking_param:{name}" for name in detail.get("removed_params", [])]
    if detail.get("fragment_removed"):
        hits.append("fragment_ignored")
    if detail.get("query_sorted"):
        hits.append("query_sorted")
    hits.extend(detail.get("path_rules", []))
    if detail.get("host_lowercased"):
        hits.append("host_lowercased")
    if detail.get("default_port_removed"):
        hits.append("default_port_stripped")
    hits.extend(
        f"pagination_preserved:{name}"
        for name in detail.get("preserved_pagination", [])
    )
    return hits


def host_of(url):
    """小写主机名（IPv6 带方括号），用于分组与告警。"""
    parts = urlsplit((url or "").strip())
    host, _ = _split_host_port(parts.netloc.rpartition("@")[2])
    return host.lower()


def site_of(url):
    """站点标识：小写主机名，非默认端口一并保留；相对地址归入空站点。"""
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    host, port = _split_host_port(parts.netloc.rpartition("@")[2])
    host = host.lower()
    if port and _DEFAULT_PORTS.get(scheme) == port:
        port = ""
    return f"{host}:{port}" if port else host


def _normalize_netloc(netloc, scheme, detail):
    userinfo, at, hostport = netloc.rpartition("@")
    host, port = _split_host_port(hostport)
    if host != host.lower():
        detail["host_lowercased"] = True
    host = host.lower()
    if port and _DEFAULT_PORTS.get(scheme) == port:
        detail["default_port_removed"] = True
        port = ""
    if not host:
        return netloc
    return f"{userinfo}{at}{host}{':' + port if port else ''}"


def _normalize_path(path, rules, detail):
    if not path or not rules.normalize_path:
        return path
    normalized = _collapse_slashes(path)
    if normalized != path:
        detail["path_rules"].append("path_collapse_slashes")
    if rules.strip_trailing_slash and normalized != "/":
        stripped = normalized.rstrip("/")
        if stripped != normalized:
            detail["path_rules"].append("path_strip_trailing_slash")
            normalized = stripped
    # 根路径的 "/" 与空路径视为同一目标。
    return "" if normalized == "/" else normalized


def _normalize_query(query, rules, detail):
    if not query:
        return ""
    kept = []
    for name, value in parse_qsl(query, keep_blank_values=True):
        if rules.is_tracking(name):
            detail["removed_params"].append(name)
            continue
        kept.append((name, value))
        if name.lower() in rules.pagination_params:
            detail["preserved_pagination"].append(name)
    if rules.sort_query:
        ordered = sorted(kept)
        detail["query_sorted"] = ordered != kept
        kept = ordered
    return urlencode(kept, doseq=True)


def _split_host_port(hostport):
    if not hostport:
        return "", ""
    if hostport.startswith("["):  # IPv6
        host, _, rest = hostport.partition("]")
        host += "]"
        port = rest[1:] if rest.startswith(":") else ""
        return host, port
    host, sep, port = hostport.partition(":")
    return host, port if sep else ""


def _collapse_slashes(path):
    while "//" in path:
        path = path.replace("//", "/")
    return path
