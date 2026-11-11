"""Canonicalization helpers shared by every rule check."""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")
_SLASH_RE = re.compile(r"/{2,}")
_MAX_DECODE_PASSES = 5


def decode_unicode_escapes(text):
    """Decode literal ``\\uXXXX`` / ``\\UXXXXXXXX`` sequences in ``text``."""

    def replace(match):
        digits = match.group(1) or match.group(2)
        return chr(int(digits, 16))

    return _UNICODE_ESCAPE_RE.sub(replace, text)


def canonical_text(value):
    """Fully decode percent/unicode escape layers and lowercase.

    Repeated passes collapse layered encodings such as
    ``%252f`` -> ``%2f`` -> ``/`` and ``\\u00252f`` -> ``%2f`` -> ``/``.
    """
    current = str(value)
    for _ in range(_MAX_DECODE_PASSES):
        decoded = decode_unicode_escapes(unquote(current))
        if decoded == current:
            break
        current = decoded
    return current.lower()


def collapse_slashes(path):
    return _SLASH_RE.sub("/", path)


def normalize_path(path):
    """Remove dot segments, fold duplicate slashes and keep a root slash."""
    if not path:
        return "/"
    path = collapse_slashes(path)
    trailing_slash = path.endswith("/")
    stack = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if stack:
                stack.pop()
            continue
        stack.append(segment)
    result = "/" + "/".join(stack)
    if trailing_slash and not result.endswith("/"):
        result += "/"
    return result


@dataclass(frozen=True)
class QueryEntry:
    index: int
    name: str
    value: str
    canonical_name: str
    canonical_value: str


def split_query(query):
    """Split a query string while keeping every repeated parameter."""
    entries = []
    if not query:
        return entries
    for index, piece in enumerate(query.split("&")):
        if not piece:
            continue
        name, separator, value = piece.partition("=")
        entries.append(
            QueryEntry(
                index=index,
                name=name,
                value=value if separator else "",
                canonical_name=canonical_text(name),
                canonical_value=canonical_text(value),
            )
        )
    return entries


@dataclass(frozen=True)
class CanonicalURL:
    original: str
    canonical: str
    path: str
    query: tuple


def normalize_url(url):
    parts = urlsplit(url)
    raw_path = parts.path or "/"
    canonical_path = normalize_path(canonical_text(raw_path))
    entries = tuple(split_query(parts.query))
    canonical_query = "&".join(
        f"{entry.canonical_name}={entry.canonical_value}" for entry in entries
    )
    canonical = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            canonical_path,
            canonical_query,
            "",
        )
    )
    return CanonicalURL(
        original=url,
        canonical=canonical,
        path=canonical_path,
        query=entries,
    )
