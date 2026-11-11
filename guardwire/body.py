"""Chunked transfer decoding and request-body parsing."""

import json
from dataclasses import dataclass, field
from urllib.parse import unquote_plus

from .normalize import canonical_text, split_query


class MalformedBody(ValueError):
    """Raised when a body cannot be decoded under its declared content type."""


def dechunk(data):
    """Reassemble an RFC 7230 chunked transfer-encoded payload."""
    output = bytearray()
    position = 0
    total = len(data)
    while True:
        line_end = data.find(b"\n", position)
        if line_end == -1:
            raise MalformedBody("truncated chunk size line")
        size_line = data[position:line_end].rstrip(b"\r")
        if not size_line:
            raise MalformedBody("empty chunk size line")
        size_spec = size_line.split(b";", 1)[0].strip()
        try:
            chunk_size = int(size_spec, 16)
        except ValueError:
            raise MalformedBody("invalid chunk size") from None
        if chunk_size < 0:
            raise MalformedBody("negative chunk size")
        position = line_end + 1
        if position + chunk_size > total:
            raise MalformedBody("truncated chunk data")
        output.extend(data[position:position + chunk_size])
        position += chunk_size
        terminator = data.find(b"\n", position)
        if terminator == -1:
            raise MalformedBody("missing chunk terminator")
        delimiter = data[position:terminator + 1]
        if delimiter not in (b"\r\n", b"\n"):
            raise MalformedBody("invalid chunk terminator")
        position = terminator + 1
        if chunk_size == 0:
            break
    return bytes(output)


@dataclass(frozen=True)
class BodyLeaf:
    field: str
    text: str
    canonical: str


def _flatten_json(value, prefix, leaves):
    if isinstance(value, dict):
        if not value:
            leaves.append(BodyLeaf(prefix, "{}", canonical_text("{}")))
        for key, item in value.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}" if prefix else key_text
            _flatten_json(item, child, leaves)
    elif isinstance(value, list):
        if not value:
            leaves.append(BodyLeaf(prefix, "[]", canonical_text("[]")))
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            _flatten_json(item, child, leaves)
    else:
        text = "null" if value is None else str(value)
        leaves.append(BodyLeaf(prefix, text, canonical_text(text)))


def _form_leaves(text):
    leaves = []
    for entry in split_query(text):
        raw_name = unquote_plus(entry.name)
        raw_value = unquote_plus(entry.value)
        leaves.append(
            BodyLeaf(
                field=raw_name,
                text=raw_value,
                canonical=canonical_text(raw_value),
            )
        )
    return leaves


@dataclass(frozen=True)
class ParsedBody:
    kind: str
    text: str
    leaves: tuple = field(default_factory=tuple)


def parse_body(raw, content_type, transfer_encoding):
    """Reassemble chunks, then parse JSON/form/plain-text bodies."""
    if transfer_encoding and "chunked" in transfer_encoding.lower():
        raw = dechunk(raw)
    text = raw.decode("utf-8", errors="replace")
    if not raw:
        return ParsedBody(kind="empty", text=text)
    content_type = (content_type or "").lower()
    if "application/json" in content_type:
        try:
            document = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise MalformedBody(str(exc)) from exc
        leaves = []
        _flatten_json(document, "", leaves)
        return ParsedBody(kind="json", text=text, leaves=tuple(leaves))
    if "application/x-www-form-urlencoded" in content_type:
        return ParsedBody(kind="form", text=text, leaves=tuple(_form_leaves(text)))
    return ParsedBody(kind="text", text=text)
