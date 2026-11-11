"""Request inspection primitives for the gateway baseline.

``inspect_request`` takes a :class:`Request` and an ordered list of
:class:`Rule` objects.  Before any rule is evaluated the request is reduced
to one canonical representation:

* percent/``%u``/``\\u``/``\\x`` escapes are decoded repeatedly;
* path dot segments, duplicate slashes and backslashes are folded;
* everything that is matched (method, path, header/parameter names and
  values, body values) is lower-cased.

The body is reassembled from chunks first, then parsed according to its
declared/sniffed media type (JSON, urlencoded form or plain text).  JSON is
walked recursively and duplicate object keys keep every value.  A body that
claims to be JSON but cannot be parsed is rejected outright.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


@dataclass
class Request:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    # ``body`` stays ``bytes`` in the public API.  A sequence of byte chunks
    # (``list``/``tuple``/iterable) is also accepted at runtime so callers can
    # model a chunked upload without changing the documented surface.
    body: bytes = b""


def _condition_mapping(value) -> Dict[str, str]:
    """Accept a mapping, a single name, or an iterable of names."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): ("" if expected is None else expected) for key, expected in value.items()}
    if isinstance(value, str):
        return {value: ""}
    try:
        return {str(item): "" for item in value}
    except TypeError:
        return {str(value): ""}


@dataclass(init=False)
class Rule:
    """A rule combines any of path/method/header/query/body conditions.

    Every supplied condition must match (logical AND).  ``needle`` remains a
    generic keyword search across the whole normalized request for backwards
    compatibility.  ``headers``, ``query`` and ``body_fields`` map a name (or
    a dotted JSON path) to an expected value; an empty expected value only
    requires the name to be present.
    """

    rule_id: str
    path_prefix: str = ""
    method: str = ""
    needle: str = ""
    action: str = "deny"
    headers: Dict[str, str] = field(default_factory=dict)
    query: Dict[str, str] = field(default_factory=dict)
    body_field: str = ""
    body_value: str = ""
    body_fields: Dict[str, str] = field(default_factory=dict)

    def __init__(
        self,
        rule_id: str,
        path_prefix: str = "",
        method: str = "",
        needle: str = "",
        action: str = "deny",
        headers: Optional[Dict[str, str]] = None,
        query: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        body_field: str = "",
        body_value: str = "",
        body_fields: Optional[Dict[str, str]] = None,
        header: Optional[Dict[str, str]] = None,
        path: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> None:
        if kwargs:
            raise TypeError(
                "unexpected Rule keyword(s): %s" % ", ".join(sorted(kwargs))
            )
        self.rule_id = rule_id
        self.path_prefix = path_prefix or path or ""
        self.method = method
        self.needle = needle
        self.action = action

        merged_headers: Dict[str, str] = {}
        merged_headers.update(_condition_mapping(header))
        merged_headers.update(_condition_mapping(headers))
        self.headers = merged_headers

        merged_query: Dict[str, str] = {}
        merged_query.update(_condition_mapping(query_params))
        merged_query.update(_condition_mapping(params))
        merged_query.update(_condition_mapping(query))
        self.query = merged_query

        merged_body: Dict[str, str] = {}
        if isinstance(body, str) and not body_field:
            body_field = body
        elif body is not None:
            merged_body.update(_condition_mapping(body))
        merged_body.update(_condition_mapping(body_fields))
        self.body_field = body_field
        self.body_value = body_value
        self.body_fields = merged_body


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_MAX_DECODE_ROUNDS = 8
_PERCENT_U = re.compile(r"%u([0-9A-Fa-f]{4})")
_UNICODE_ESCAPE = re.compile(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|x[0-9A-Fa-f]{2})")
_DUPLICATE_SLASH = re.compile(r"/+")


def _escape_replacement(match) -> str:
    token = match.group(1)
    digits = token[1:]
    try:
        code = int(digits, 16)
    except ValueError:
        return match.group(0)
    if code > 0x10FFFF:
        return match.group(0)
    try:
        return chr(code)
    except ValueError:
        return match.group(0)


def _normalize_encoding(text, plus: bool = False) -> str:
    """Decode percent/Unicode escapes until the value stops changing."""
    if not isinstance(text, str):
        text = str(text)
    if plus:
        # ``+`` is a query-space marker; apply it once so an encoded ``%2B``
        # (a literal plus) is not turned into a space by later rounds.
        text = text.replace("+", " ")
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = _PERCENT_U.sub(_escape_replacement, text)
        decoded = unquote(decoded)
        decoded = _UNICODE_ESCAPE.sub(_escape_replacement, decoded)
        if decoded == text:
            break
        text = decoded
    # A NUL does not belong in a gateway-level comparison and is a common
    # prefix-matching bypass; drop it from the canonical form.
    return text.replace("\x00", "")


def _normalize_feature(text, plus: bool = False) -> str:
    return _normalize_encoding(text, plus=plus).lower()


def _normalize_needle(text) -> str:
    return _normalize_encoding(text).strip().lower()


def _normalize_method(method) -> str:
    return str(method or "").strip().upper()


def _normalize_path(path) -> str:
    """Collapse escaping, dot segments, repeated slashes and case."""
    path = _normalize_encoding(path)
    path = path.replace("\\", "/")
    path = _DUPLICATE_SLASH.sub("/", path)
    segments: List[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments and segments[-1] != "..":
                segments.pop()
            # Clamp at the root: ``/../../admin`` folds to ``/admin``.
            continue
        segments.append(segment)
    return ("/" + "/".join(segments)).lower()


def _split_url(url: str) -> Tuple[str, str]:
    """Return ``(raw_path, raw_query)`` for origin-form and absolute URLs."""
    if not isinstance(url, str):
        url = str(url)
    if url.startswith("//"):
        # Scheme-relative-looking input is treated as a path so that
        # ``//admin`` folds to ``/admin`` instead of becoming a host.
        raw = url.split("#", 1)[0]
        return tuple(raw.split("?", 1)) if "?" in raw else (raw, "")
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        return parsed.path or "/", parsed.query
    raw = url.split("#", 1)[0]
    if "?" in raw:
        return tuple(raw.split("?", 1))
    return raw, ""


# ---------------------------------------------------------------------------
# Parsed request pieces
# ---------------------------------------------------------------------------


@dataclass
class _Param:
    name: str
    value: str
    original_pair: str


@dataclass
class _Header:
    name: str
    value: str
    original_value: str


@dataclass
class _BodyField:
    path: str
    value: str
    original: str


class _JsonObject(list):
    """An object with all key/value pairs preserved (duplicates included)."""

    __slots__ = ()


def _canonical_field_path(path) -> str:
    """Make ``users[0].role`` and ``users.0.role`` the same field path."""
    path = _normalize_feature(str(path)).strip()
    path = re.sub(r"\[([0-9]+)\]", r".\1", path)
    return re.sub(r"\.+", ".", path).strip(".")


@dataclass
class _BodyInfo:
    kind: str = "text"
    raw_text: str = ""
    normalized_text: str = ""
    fields: List[_BodyField] = field(default_factory=list)
    parse_error: Optional[str] = None

    def fields_named(self, wanted: str) -> List[_BodyField]:
        wanted = _canonical_field_path(wanted)
        found: List[_BodyField] = []
        for item in self.fields:
            actual = _canonical_field_path(item.path)
            if not wanted:
                if actual:
                    found.append(item)
                continue
            if actual == wanted or actual.endswith("." + wanted):
                found.append(item)
        return found


@dataclass
class _NormalizedRequest:
    method: str
    raw_path: str
    path: str
    params: List[_Param]
    headers: List[_Header]
    body: _BodyInfo

    def query_groups(self) -> Dict[str, List[_Param]]:
        groups: Dict[str, List[_Param]] = {}
        for param in self.params:
            groups.setdefault(param.name, []).append(param)
        return groups

    def header_groups(self) -> Dict[str, List[_Header]]:
        groups: Dict[str, List[_Header]] = {}
        for header in self.headers:
            groups.setdefault(header.name, []).append(header)
        return groups

    def normalized_url(self) -> str:
        if not self.params:
            return self.path
        pairs = []
        for param in self.params:
            pairs.append("%s=%s" % (param.name, param.value))
        return self.path + "?" + "&".join(pairs)


# ---------------------------------------------------------------------------
# Body reassembly and parsing
# ---------------------------------------------------------------------------


def _header_lookup(headers: Dict[str, str], name: str) -> str:
    wanted = name.strip().lower()
    for key, value in (headers or {}).items():
        if str(key).strip().lower() == wanted:
            return str(value)
    return ""


def _as_bytes(chunk) -> bytes:
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        return bytes(chunk)
    return str(chunk).encode("utf-8")


def _dechunk(body: bytes) -> Optional[bytes]:
    """Decode HTTP ``Transfer-Encoding: chunked`` framing if it is valid."""
    position = 0
    chunks: List[bytes] = []
    length = len(body)
    while position < length:
        line_end = body.find(b"\n", position)
        if line_end < 0:
            return None
        line = body[position:line_end].rstrip(b"\r")
        size_text = line.split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            return None
        position = line_end + 1
        if size == 0:
            return b"".join(chunks)
        end = position + size
        if end > length:
            return None
        chunks.append(body[position:end])
        position = end
        if body[position : position + 2] == b"\r\n":
            position += 2
        elif body[position : position + 1] == b"\n":
            position += 1
        elif position < length:
            return None
    return None


def _collect_body(request: Request) -> bytes:
    raw = request.body
    if raw is None:
        return b""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
    elif isinstance(raw, str):
        data = raw.encode("utf-8")
    elif isinstance(raw, Iterable):
        data = b"".join(_as_bytes(part) for part in raw)
    else:
        data = str(raw).encode("utf-8")

    transfer_encoding = _header_lookup(request.headers, "transfer-encoding")
    if "chunked" in transfer_encoding.lower() and data:
        decoded = _dechunk(data)
        # Adapters frequently expose an already-dechunked body while leaving
        # the header in place; a malformed frame therefore falls back to the
        # bytes we were given rather than losing the request body.
        if decoded is not None:
            data = decoded
    return data


def _classify_body(text: str, content_type: str) -> str:
    if not text.strip():
        return "text"
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype.endswith("+json") or "json" in ctype:
        return "json"
    if ctype == "application/x-www-form-urlencoded":
        return "form"
    if ctype.startswith("text/"):
        return "text"

    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if "=" in text and ("&" in text or ";" in text or "\n" not in text):
        return "form"
    return "text"


def _load_json(text: str):
    candidates = [text]
    decoded = _normalize_encoding(text)
    if decoded != text:
        # Handles bodies that were themselves percent-encoded.  The original
        # text is always tried first so ordinary string values are untouched.
        candidates.append(decoded)
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            return json.loads(candidate, object_pairs_hook=_JsonObject), None
        except (ValueError, TypeError) as error:
            last_error = error
    return None, last_error


def _scalar_text(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return _normalize_encoding(str(value)).lower()


def _json_raw_fragment(text: str, value) -> str:
    """Return a raw slice of the JSON body around a matched leaf value."""
    if value is None:
        needle = "null"
    elif value is True:
        needle = "true"
    elif value is False:
        needle = "false"
    else:
        needle = str(value)
    if needle:
        position = text.lower().find(needle.lower())
        if position >= 0:
            start = max(0, position - 40)
            end = min(len(text), position + len(needle) + 40)
            return text[start:end]
    return text[:120]


def _flatten_json(value, prefix: str, output: List[_BodyField], original: str) -> None:
    if isinstance(value, _JsonObject):
        for key, item in value:
            key_text = _normalize_encoding(str(key)).strip().lower()
            child = ("%s.%s" % (prefix, key_text)) if prefix else key_text
            _flatten_json(item, child, output, original)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            child = ("%s[%d]" % (prefix, index)) if prefix else "[%d]" % index
            _flatten_json(item, child, output, original)
        return
    if not prefix:
        return
    output.append(
        _BodyField(prefix, _scalar_text(value), _json_raw_fragment(original, value))
    )


def _parse_params(raw: str) -> List[_Param]:
    params: List[_Param] = []
    for pair in re.split(r"[&;]", raw):
        if pair == "":
            continue
        if "=" in pair:
            raw_name, raw_value = pair.split("=", 1)
        else:
            raw_name, raw_value = pair, ""
        name = _normalize_encoding(raw_name, plus=True).strip().lower()
        value = _normalize_encoding(raw_value, plus=True).strip().lower()
        params.append(_Param(name, value, pair))
    return params


def _parse_body(text: str, content_type: str) -> _BodyInfo:
    kind = _classify_body(text, content_type)
    info = _BodyInfo(kind=kind, raw_text=text)
    info.normalized_text = _normalize_encoding(text).lower()

    if kind == "json":
        if not text.strip():
            info.kind = "text"
            return info
        data, error = _load_json(text)
        if error is not None:
            info.parse_error = "invalid JSON body: %s" % error
            return info
        _flatten_json(data, "", info.fields, text)
        return info

    if kind == "form":
        for param in _parse_params(text):
            info.fields.append(_BodyField(param.name, param.value, param.original_pair))
        return info

    return info


def _normalize_request(request: Request, body_bytes: bytes) -> _NormalizedRequest:
    raw_path, raw_query = _split_url(request.url)
    path = _normalize_path(raw_path)

    headers: List[_Header] = []
    for name, value in (request.headers or {}).items():
        original = str(value)
        headers.append(
            _Header(
                _normalize_encoding(str(name)).strip().lower(),
                _normalize_encoding(original).strip().lower(),
                original,
            )
        )

    params = _parse_params(raw_query)
    body_text = body_bytes.decode("utf-8", errors="ignore")
    content_type = _header_lookup(request.headers, "content-type")
    body = _parse_body(body_text, content_type)

    return _NormalizedRequest(
        method=_normalize_method(request.method),
        raw_path=raw_path,
        path=path,
        params=params,
        headers=headers,
        body=body,
    )


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class _PathNode:
    __slots__ = ("children", "rules")

    def __init__(self) -> None:
        self.children: Dict[str, "_PathNode"] = {}
        self.rules: List[Tuple[int, Rule]] = []


class _MethodPathIndex:
    """A method-keyed trie of normalized path prefixes."""

    def __init__(self) -> None:
        self.roots: Dict[str, _PathNode] = {}

    def add(self, method: str, path_prefix: str, order: int, rule: Rule) -> None:
        key = method or "*"
        root = self.roots.setdefault(key, _PathNode())
        node = root
        for character in path_prefix:
            node = node.children.setdefault(character, _PathNode())
        node.rules.append((order, rule))

    def candidates(self, method: str, path: str) -> List[Tuple[int, Rule]]:
        found: List[Tuple[int, Rule]] = []
        for key in (method, "*"):
            node = self.roots.get(key)
            if node is None:
                continue
            found.extend(node.rules)
            for character in path:
                node = node.children.get(character)
                if node is None:
                    break
                found.extend(node.rules)
        seen: set = set()
        unique: List[Tuple[int, Rule]] = []
        for order, rule in sorted(found, key=lambda item: item[0]):
            if (order, id(rule)) in seen:
                continue
            seen.add((order, id(rule)))
            unique.append((order, rule))
        return unique


class _RuleIndex:
    def __init__(self, rules: List[Rule]) -> None:
        self._path_index = _MethodPathIndex()
        for order, rule in enumerate(rules):
            method = _normalize_method(rule.method)
            prefix = _normalize_path(rule.path_prefix) if rule.path_prefix else ""
            self._path_index.add(method, prefix, order, rule)

    def candidates(self, method: str, path: str) -> List[Tuple[int, Rule]]:
        return self._path_index.candidates(method, path)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _match_dict(
    field_name: str,
    key: str,
    normalized: str,
    original: str,
    index: int = 0,
) -> dict:
    return {
        "field": field_name,
        "key": key,
        "index": index,
        "normalized_fragment": normalized,
        "original_fragment": original,
    }


_MATCH_PRIORITY = {"body": 0, "query": 1, "header": 2, "needle": 3, "path": 4, "method": 5}


def _best_match(matches: List[dict]) -> dict:
    if not matches:
        return {}
    return min(
        enumerate(matches),
        key=lambda item: (_MATCH_PRIORITY.get(item[1]["field"], 99), item[0]),
    )[1]


def _find_needle(normalized: _NormalizedRequest, needle: str) -> Optional[dict]:
    if not needle:
        return None
    if needle in normalized.path:
        return _match_dict("path", "", normalized.path, normalized.raw_path)
    if needle in normalized.method.lower():
        return _match_dict("method", "", normalized.method.lower(), normalized.method)

    for param in normalized.params:
        if needle in param.name:
            return _match_dict("query", param.name, param.name, param.original_pair)
        if needle in param.value:
            return _match_dict("query", param.name, param.value, param.original_pair)

    for header in normalized.headers:
        if needle in header.name:
            return _match_dict("header", header.name, header.name, header.original_value)
        if needle in header.value:
            return _match_dict("header", header.name, header.value, header.original_value)

    for item in normalized.body.fields:
        if needle in item.path:
            return _match_dict("body", item.path, item.path, item.original)
        if needle in item.value:
            return _match_dict("body", item.path, item.value, item.original)

    if needle in normalized.body.normalized_text:
        text = normalized.body.normalized_text
        return _match_dict("needle", "", needle, text[:120])

    return None


def _match_rule(rule: Rule, normalized: _NormalizedRequest) -> Optional[dict]:
    matches: List[dict] = []

    wanted_method = _normalize_method(rule.method)
    if wanted_method and wanted_method != normalized.method:
        return None
    if wanted_method:
        matches.append(_match_dict("method", "", normalized.method, normalized.method))

    if rule.path_prefix:
        prefix = _normalize_path(rule.path_prefix)
        if not normalized.path.startswith(prefix):
            return None
        matches.append(_match_dict("path", "", normalized.path, normalized.raw_path))

    for key, expected in (rule.headers or {}).items():
        wanted_name = _normalize_encoding(str(key)).strip().lower()
        wanted_value = _normalize_needle(expected)
        matched = False
        for header in normalized.headers:
            if header.name != wanted_name:
                continue
            if wanted_value == "" or wanted_value in header.value:
                matches.append(
                    _match_dict("header", header.name, header.value, header.original_value)
                )
                matched = True
        if not matched:
            return None

    for key, expected in (rule.query or {}).items():
        wanted_name = _normalize_encoding(str(key)).strip().lower()
        wanted_value = _normalize_needle(expected)
        matched = False
        for index, param in enumerate(normalized.params):
            if param.name != wanted_name:
                continue
            if wanted_value == "" or wanted_value in param.value:
                matches.append(
                    _match_dict("query", param.name, param.value, param.original_pair, index)
                )
                matched = True
        if not matched:
            return None

    body_conditions: List[Tuple[str, str]] = []
    if rule.body_field:
        body_conditions.append((rule.body_field, rule.body_value))
    for path, value in (rule.body_fields or {}).items():
        body_conditions.append((path, value))

    for path, expected in body_conditions:
        wanted_value = _normalize_needle(expected)
        fields = normalized.body.fields_named(path)
        if not fields:
            return None
        matched = False
        for item in fields:
            if wanted_value == "" or wanted_value in item.value:
                matches.append(_match_dict("body", item.path, item.value, item.original))
                matched = True
        if not matched:
            return None

    if rule.needle and rule.needle.strip():
        found = _find_needle(normalized, _normalize_needle(rule.needle))
        if found is None:
            return None
        matches.append(found)

    return _best_match(matches)


def _normalized_evidence(normalized: _NormalizedRequest) -> dict:
    query = {name: [param.value for param in values] for name, values in normalized.query_groups().items()}
    headers = {
        name: [header.value for header in values]
        for name, values in normalized.header_groups().items()
    }
    fields: Dict[str, List[str]] = {}
    for item in normalized.body.fields:
        fields.setdefault(item.path, []).append(item.value)
    body = {
        "kind": normalized.body.kind,
        "text": normalized.body.normalized_text[:1024],
        "fields": fields,
    }
    return {
        "method": normalized.method,
        "path": normalized.path,
        "url": normalized.normalized_url(),
        "query": query,
        "headers": headers,
        "body": body,
    }


def _result(allowed: bool, rule_id, match: dict, normalized: _NormalizedRequest, url: str) -> dict:
    evidence = {
        "url": url,
        "body": normalized.body.raw_text[:80],
        "normalized": _normalized_evidence(normalized),
        "match": match,
        "normalized_path": normalized.path,
        "normalized_url": normalized.normalized_url(),
        "normalized_fragment": match.get("normalized_fragment", ""),
        "original_fragment": match.get("original_fragment", ""),
        "raw_fragment": match.get("original_fragment", ""),
    }
    if normalized.body.parse_error:
        evidence["reason"] = normalized.body.parse_error
    result = {
        "allowed": allowed,
        "rule_id": rule_id,
        "evidence": evidence,
        "normalized": evidence["normalized"],
        "match": match,
        "original_fragment": evidence["original_fragment"],
    }
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def inspect_request(request: Request, rules: List[Rule]) -> dict:
    """Inspect one request against ``rules`` in order and return a verdict."""
    body_bytes = _collect_body(request)
    normalized = _normalize_request(request, body_bytes)

    if normalized.body.parse_error:
        match = _match_dict(
            "body", "", normalized.body.normalized_text[:120], normalized.body.raw_text[:120]
        )
        return _result(False, "body-parse-error", match, normalized, request.url)

    index = _RuleIndex(rules or [])
    for _order, rule in index.candidates(normalized.method, normalized.path):
        match = _match_rule(rule, normalized)
        if match is not None:
            return _result(rule.action != "deny", rule.rule_id, match, normalized, request.url)

    return _result(True, None, {}, normalized, request.url)
