"""URL normalisation used to decide whether two addresses point at one target.

The rules deliberately keep path and pagination untouched: only tracking
parameters, parameter order and the fragment are discarded.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Default ports that carry no information and are dropped from the netloc.
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}

# Common advertising / analytics parameters.  Names are matched case
# insensitively; pagination names are never treated as tracking even if a
# caller configures them by mistake.
DEFAULT_TRACKING_PARAMS = frozenset(
    {
        "_ga",
        "_gl",
        "_hsenc",
        "_hsmi",
        "_openstat",
        "aff_id",
        "affiliate_id",
        "assetid",
        "assettype",
        "campaignid",
        "campaign_id",
        "dclid",
        "elqtrack",
        "elqtrackid",
        "fbclid",
        "gclid",
        "hsctatracking",
        "igshid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "msclkid",
        "oly_anon_id",
        "oly_enc_id",
        "rb_clickid",
        "ref",
        "ref_src",
        "ref_url",
        "referer",
        "referrer",
        "s_cid",
        "sc_cid",
        "scm",
        "share_source",
        "share_token",
        "spm",
        "trk",
        "trkcampaign",
        "twclid",
        "vero_id",
        "yclid",
    }
)

DEFAULT_TRACKING_PREFIXES = (
    "_ga_",
    "elq",
    "fb_",
    "ga_",
    "hsa_",
    "icid_",
    "mc_",
    "mtm_",
    "oly_",
    "pk_",
    "sc_",
    "trk_",
    "utm_",
    "vero_",
)

# Kept explicitly: paging / ordering changes the content that is fetched.
DEFAULT_PAGINATION_PARAMS = frozenset(
    {
        "after",
        "before",
        "cursor",
        "dir",
        "limit",
        "offset",
        "order",
        "p",
        "page",
        "page_num",
        "page_number",
        "pageno",
        "pagenum",
        "pagesize",
        "page_size",
        "per-page",
        "per_page",
        "pg",
        "pn",
        "ps",
        "sort",
        "start",
        "startat",
    }
)


@dataclass
class NormalizeRules:
    """Mutable rule set, owned by the store so it can be reloaded per site."""

    tracking_params: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_TRACKING_PARAMS)
    )
    tracking_prefixes: tuple[str, ...] = field(
        default_factory=lambda: tuple(DEFAULT_TRACKING_PREFIXES)
    )
    pagination_params: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_PAGINATION_PARAMS)
    )

    def is_tracking(self, name: str) -> bool:
        low = name.lower()
        if low in self.pagination_params:
            return False
        if low in self.tracking_params:
            return True
        return any(low.startswith(prefix) for prefix in self.tracking_prefixes)


@dataclass(frozen=True)
class Normalization:
    """Result of normalising one raw address."""

    raw: str
    url: str
    site: str
    rules: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]


def normalize(url, rules: NormalizeRules | None = None) -> Normalization:
    """Return the canonical form of *url* plus the rules that changed it.

    The order of the retained query parameters is stabilised and tracking
    parameters are removed.  Path (and its case), pagination parameters and
    the rest of the query are preserved.
    """
    rules = rules or NormalizeRules()
    raw = "" if url is None else str(url)
    text = raw.strip()
    if not text:
        return Normalization(raw=raw, url="", site="", rules=())

    try:
        parts = urlsplit(text)
    except ValueError:
        return Normalization(raw=raw, url=text, site="", rules=("unparseable",))

    changes: list[str] = []
    scheme = parts.scheme.lower()
    if parts.scheme and parts.scheme != scheme:
        changes.append("scheme-lowercased")

    netloc, host, host_out, port_removed = _netloc(parts)
    if netloc != parts.netloc:
        if port_removed:
            changes.append("default-port-removed")
        raw_host = parts.netloc.rsplit("@", 1)[-1]
        if raw_host.startswith("["):
            raw_host = raw_host.split("]", 1)[0] + "]"
        else:
            raw_host = raw_host.split(":")[0]
        if host and raw_host != host_out:
            changes.append("host-lowercased")

    if parts.fragment:
        changes.append("fragment-stripped")

    path = parts.path
    if parts.netloc and not path:
        path = "/"
        changes.append("path-defaulted")

    kept: list[tuple[str, str]] = []
    dropped: set[str] = set()
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if rules.is_tracking(name):
            dropped.add(name.lower())
        else:
            kept.append((name, value))
    if dropped:
        changes.append("tracking-params-dropped:" + ",".join(sorted(dropped)))

    # Sort by name then value so that reordering alone never changes the
    # fingerprint; the sort is total, so repeated names are stable too.
    ordered = sorted(kept, key=lambda item: (item[0].lower(), item[0], item[1]))
    if ordered != kept:
        changes.append("query-sorted")
    query = urlencode(ordered, doseq=True, quote_via=quote)

    normalized = urlunsplit((scheme, netloc, path, query, ""))
    if normalized != text and not changes:
        changes.append("url-normalized")

    return Normalization(
        raw=raw,
        url=normalized,
        site=(parts.hostname or "").lower(),
        rules=tuple(changes),
    )


def _netloc(parts) -> tuple[str, str, str, bool]:
    """Rebuild a lowercase netloc, dropping a default port.

    Returns ``(netloc, hostname, host_with_brackets, port_was_removed)``.
    """
    userinfo = ""
    hostport = parts.netloc
    if "@" in hostport:
        userinfo, hostport = hostport.rsplit("@", 1)
        userinfo += "@"

    host = parts.hostname or ""
    host_out = f"[{host}]" if ":" in host and not host.startswith("[") else host

    try:
        port = parts.port
    except ValueError:
        # Keep an unparseable port verbatim; the host is still normalised.
        port = None
        if hostport.startswith("[") and "]" in hostport:
            remainder = hostport.split("]", 1)[1]
        else:
            remainder = hostport[len(host):] if hostport.startswith(host) else ""
        if remainder.startswith(":"):
            return f"{userinfo}{host_out}{remainder}", host, host_out, False
        return f"{userinfo}{host_out}", host, host_out, False

    if port is not None and port == _DEFAULT_PORTS.get(parts.scheme.lower()):
        return f"{userinfo}{host_out}", host, host_out, True
    if port is not None:
        return f"{userinfo}{host_out}:{port}", host, host_out, False
    return f"{userinfo}{host_out}", host, host_out, False
