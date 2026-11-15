"""URL normalisation and target identity.

Two seeds point at the same target when, after normalisation, they share a
host, path and meaningful query.  Scheme, ``www.``, default ports, fragments,
tracking parameters and ordering of query parameters do not make a new
target.  This is what lets the admission filter keep only the best seed for a
duplicate target.
"""

from __future__ import annotations

import ipaddress
import posixpath
import re
from typing import Iterable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
ALLOWED_SCHEMES = {"http", "https"}

_DEFAULT_TRACKING_PARAMS = {
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "yclid", "spm", "scid", "cmpid", "_ga", "_gl", "ref", "ref_src",
    "referrer", "source", "campaign", "affiliate",
}
_DEFAULT_TRACKING_PREFIXES = ("utm_",)
_DEFAULT_SESSION_PARAMS = {
    "sessionid", "session_id", "phpsessid", "jsessionid", "aspsessionid",
    "sid", "token", "auth", "password", "pwd", "email",
}

_INDEX_FILES = {
    "index.html", "index.htm", "index.php", "index.asp", "index.jsp",
    "default.html", "default.htm", "default.asp", "default.aspx",
}

_UNRESERVED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
)
_PCT_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_SLASH_RE = re.compile(r"/{2,}")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d+)?(?:/|$)")

_SECOND_LEVEL = {
    "ac", "co", "com", "edu", "go", "gob", "gov", "ltd", "mil", "ne",
    "net", "or", "org", "plc", "sch",
}


class URLError(ValueError):
    """Raised when a seed URL cannot be interpreted as an HTTP(S) URL."""


def _option_set(values: Iterable[str] | None, default: Iterable[str]) -> set[str]:
    if values is None:
        return set(default)
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _is_unreserved(char: str) -> bool:
    return char in _UNRESERVED


def _normalise_percent_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        if _is_unreserved(char):
            return char
        return "%" + match.group(1).upper()

    return _PCT_RE.sub(replace, value)


def _normalise_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    if not host:
        raise URLError("missing host")
    if host.startswith("[") and host.endswith("]"):
        # IPv6 literal.  Keep the brackets for urlunsplit.
        inner = host[1:-1]
        try:
            return "[" + str(ipaddress.IPv6Address(inner)) + "]"
        except ValueError as exc:
            raise URLError("invalid IPv6 host") from exc
    if any(ord(char) > 127 for char in host):
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise URLError("invalid international host") from exc
    if host.startswith("www.") and host.count(".") >= 2:
        host = host[4:]
    if not host or any(char.isspace() for char in host) or "/" in host:
        raise URLError("invalid host")
    # A public web target needs a dotted name, a valid IP literal, or the
    # explicit localhost alias.  This rejects junk such as "123".
    bare = host.strip("[]")
    if "." not in bare and not is_ip_host(bare) and bare != "localhost":
        raise URLError("invalid host")
    return host


def _normalise_path(path: str) -> str:
    path = _normalise_percent_escapes(path or "/")
    if not path.startswith("/"):
        path = "/" + path
    path = _SLASH_RE.sub("/", path)
    # posixpath.normpath collapses "." and ".." segments.
    path = posixpath.normpath(path)
    if path in {".", ""}:
        path = "/"
    if not path.startswith("/"):
        path = "/" + path
    basename = posixpath.basename(path).lower()
    if basename in _INDEX_FILES:
        parent = posixpath.dirname(path)
        path = parent or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    # Encode spaces and non-ASCII without touching already escaped bytes.
    return quote(path, safe="/:@!$&'()*+,;=-._~%")


def _normalise_query(
    query: str,
    tracking_params: Iterable[str] | None = None,
    tracking_prefixes: Iterable[str] | None = None,
    session_params: Iterable[str] | None = None,
) -> str:
    tracking = _option_set(tracking_params, _DEFAULT_TRACKING_PARAMS)
    prefixes = tuple(
        str(prefix).lower()
        for prefix in (tracking_prefixes if tracking_prefixes is not None else _DEFAULT_TRACKING_PREFIXES)
        if str(prefix)
    )
    sessions = _option_set(session_params, _DEFAULT_SESSION_PARAMS)
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(query or "", keep_blank_values=True):
        lowered = key.lower()
        if (
            lowered in tracking
            or lowered in sessions
            or any(lowered.startswith(prefix) for prefix in prefixes)
        ):
            continue
        pairs.append((key, value))
    pairs.sort(key=lambda item: (item[0].lower(), item[1]))
    return urlencode(pairs)


def _ensure_scheme(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise URLError("missing url")
    if raw.startswith("//"):
        return "http:" + raw
    if "://" not in raw:
        # Accept bare hosts such as "example.com/path" and "localhost:8080".
        if _HOST_RE.match(raw):
            return "http://" + raw
        raise URLError("missing url scheme")
    return raw


def _split(raw: str):
    try:
        return urlsplit(_ensure_scheme(raw))
    except URLError:
        raise
    except ValueError as exc:
        raise URLError(f"invalid url: {exc}") from exc


def normalise_url(
    url: str,
    *,
    tracking_params: Iterable[str] | None = None,
    tracking_prefixes: Iterable[str] | None = None,
    session_params: Iterable[str] | None = None,
) -> str:
    """Return a canonical display URL or raise :class:`URLError`."""
    parsed = _split(url)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise URLError(f"unsupported scheme: {scheme or 'missing'}")
    host = _normalise_host(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLError("invalid port") from exc
    if port is not None and not 1 <= int(port) <= 65535:
        raise URLError("invalid port")
    netloc = host
    if is_ip_host(host):
        candidate = host.strip("[]")
        if ":" in candidate:
            netloc = f"[{candidate}]"
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    path = _normalise_path(parsed.path or "/")
    query = _normalise_query(
        parsed.query,
        tracking_params=tracking_params,
        tracking_prefixes=tracking_prefixes,
        session_params=session_params,
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def try_normalise(value: str, **kwargs) -> tuple[str, str | None]:
    """Return ``(normalised_url, error)`` without raising."""
    try:
        return normalise_url(value, **kwargs), None
    except (URLError, TypeError, AttributeError) as exc:
        return "", str(exc) or "invalid url"


def parsed_parts(url: str) -> dict[str, str | int | None]:
    """Return canonical URL parts after validation."""
    normalised = normalise_url(url)
    parsed = urlsplit(normalised)
    return {
        "url": normalised,
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "port": parsed.port,
        "path": parsed.path or "/",
        "query": parsed.query,
    }


def target_key(url: str) -> str:
    """Return the identity used to collapse duplicate seeds.

    The scheme is deliberately omitted so http and https variants of the same
    page count as one target.  ``www.`` and trailing slashes are already
    removed by :func:`normalise_url`.
    """
    parsed = urlsplit(normalise_url(url))
    host = parsed.hostname or ""
    path = _normalise_path(parsed.path or "/")
    # Use the same query cleanup as normalise_url (already canonical here).
    query = parsed.query
    return f"{host}{path}" + (f"?{query}" if query else "")


def host_of(url: str) -> str:
    return (urlsplit(normalise_url(url)).hostname or "").lower()


def path_of(url: str) -> str:
    return _normalise_path(urlsplit(normalise_url(url)).path or "/")


def query_pairs(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(normalise_url(url)).query, keep_blank_values=True)


def is_ip_host(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return False


def site_of(host: str) -> str:
    """Return a stable site label for reporting/per-site overrides.

    This is a lightweight registrable-domain heuristic (no Public Suffix List
    dependency).  It handles common multi-label suffixes such as ``co.uk``.
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host or is_ip_host(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_extension(url_or_path: str) -> str:
    """Return the lower-case final extension (``.html``) or an empty string."""
    path = url_or_path
    if "://" in url_or_path:
        path = urlsplit(url_or_path).path
    basename = posixpath.basename(path.lower())
    if "." not in basename:
        return ""
    return "." + basename.rsplit(".", 1)[1]


def normalise_order(
    order: Iterable[object] | None,
    *,
    config: Mapping[str, object] | None = None,
) -> list[str]:
    """Normalise a caller-supplied queue order to target keys."""
    result: list[str] = []
    if not order:
        return result
    tracking = None
    prefixes = None
    sessions = None
    if isinstance(config, Mapping):
        tracking = config.get("tracking_params")
        prefixes = config.get("tracking_prefixes")
        sessions = config.get("session_params")
    for item in order:
        raw = item
        if isinstance(item, Mapping):
            raw = item.get("target") or item.get("url") or ""
        if not raw:
            continue
        raw = str(raw)
        normalised, error = try_normalise(
            raw,
            tracking_params=tracking,
            tracking_prefixes=prefixes,
            session_params=sessions,
        )
        key = target_key(normalised) if normalised and not error else raw
        if key not in result:
            result.append(key)
    return result
