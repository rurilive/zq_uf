"""URL 归一化规则：忽略追踪参数、参数顺序与锚点，保留路径与分页。"""
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_TRACKING_PARAMS = frozenset({
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "spm", "ref", "ref_src", "source", "campaignid",
})
DEFAULT_TRACKING_PREFIXES = ("utm_",)


@dataclass(frozen=True)
class RuleSet:
    """一版归一化规则。version 会出现在判定返回里，便于确认命中了哪版规则。"""
    version: str = "v1"
    tracking_params: frozenset = DEFAULT_TRACKING_PARAMS
    tracking_prefixes: tuple = DEFAULT_TRACKING_PREFIXES
    sort_params: bool = True
    strip_fragment: bool = True

    def is_tracking(self, name):
        n = name.lower()
        return n in self.tracking_params or any(
            n.startswith(p) for p in self.tracking_prefixes)


@dataclass
class Normalization:
    original: str
    normalized: str
    matched_rules: list   # 命中的规则 id 列表
    dropped_params: list
    why: str              # 人类可读的判定原因


def site_of(url):
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def normalize(url, rules):
    """按 rules 归一化 URL，返回 Normalization（含命中规则与原因）。"""
    matched, dropped, notes = [], [], []
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
        matched.append("add-default-scheme")
    parts = urlsplit(raw)

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.netloc != parts.netloc.lower():
        matched.append("lowercase-host")
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not (scheme == "http" and port == 80) \
            and not (scheme == "https" and port == 443):
        netloc = "%s:%d" % (host, port)
    else:
        if port:
            matched.append("strip-default-port")
        netloc = host

    path = parts.path or "/"
    if not parts.path:
        matched.append("root-path")

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if rules.is_tracking(k):
            dropped.append(k)
        else:
            kept.append((k, v))  # 路径与分页参数原样保留
    if dropped:
        matched.append("drop-tracking-param")
        notes.append("丢弃追踪参数 " + ",".join(sorted(set(dropped))))
    if rules.sort_params:
        ordered = sorted(kept)
        if ordered != kept:
            matched.append("sort-params")
            notes.append("查询参数按键名排序")
        kept = ordered

    fragment = ""
    if parts.fragment and rules.strip_fragment:
        matched.append("strip-fragment")
        notes.append("去掉页尾锚点")
    elif parts.fragment:
        fragment = parts.fragment

    normalized = urlunsplit((scheme, netloc, path,
                             urlencode(kept), fragment))
    why = "；".join(notes) if notes else "无需归一化"
    return Normalization(original=url, normalized=normalized,
                         matched_rules=matched,
                         dropped_params=dropped, why=why)
