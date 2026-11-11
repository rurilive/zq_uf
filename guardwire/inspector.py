"""Request inspection entry point for the gateway."""
import re
from dataclasses import dataclass, field
from typing import Dict, List

from .body import MalformedBody, parse_body
from .normalize import canonical_text, normalize_url
from .rules import RuleIndex, evaluate_rule

_INDEX_SUFFIX_RE = re.compile(r"\[\d+\]")


@dataclass
class Request:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass
class Rule:
    rule_id: str
    path_prefix: str = ""
    method: str = ""
    needle: str = ""
    action: str = "deny"
    header: str = ""
    query_param: str = ""
    body_field: str = ""


class _View:
    def __init__(self, method, url, headers, body):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body
        self.body_field_names = self._index_body_fields(body)
        self.scan_scopes = self._build_scopes()

    @staticmethod
    def _index_body_fields(body):
        names_by_id = {}
        for leaf in body.leaves:
            names = {canonical_text(leaf.field)}
            for dotted in leaf.field.split("."):
                plain = _INDEX_SUFFIX_RE.sub("", dotted)
                if plain:
                    names.add(canonical_text(plain))
            names_by_id[id(leaf)] = tuple(name for name in names if name)
        return names_by_id

    def _build_scopes(self):
        scopes = [{
            "location": "url",
            "original": self.url.original,
            "text": self.url.canonical,
        }]
        for header in self.headers:
            scopes.append({
                "location": "header:" + header["name"],
                "original": header["original_value"],
                "text": header["canonical_value"],
            })
        for entry in self.url.query:
            original = entry.name + "=" + entry.value
            scopes.append({
                "location": "query:" + entry.name + "#" + str(entry.index),
                "original": original,
                "text": entry.canonical_name + "=" + entry.canonical_value,
            })
        if self.body.kind in ("json", "form"):
            for leaf in self.body.leaves:
                original = leaf.field + "=" + leaf.text
                scopes.append({
                    "location": "body",
                    "original": original,
                    "text": canonical_text(original),
                })
        elif self.body.kind == "text":
            scopes.append({
                "location": "body",
                "original": self.body.text,
                "text": canonical_text(self.body.text),
            })
        return scopes


def _build_view(request):
    headers = [
        {
            "name": str(name).lower(),
            "original_value": value,
            "canonical_value": canonical_text(value),
        }
        for name, value in request.headers.items()
    ]
    lookup = {item["name"]: item["original_value"] for item in headers}
    body = parse_body(
        request.body,
        lookup.get("content-type", ""),
        lookup.get("transfer-encoding", ""),
    )
    return _View(
        method=request.method.upper(),
        url=normalize_url(request.url),
        headers=headers,
        body=body,
    )


def _normalized_evidence(view):
    return {
        "method": view.method,
        "url": view.url.canonical,
        "path": view.url.path,
    }


def inspect_request(request: Request, rules: List[Rule]) -> dict:
    try:
        view = _build_view(request)
    except MalformedBody as exc:
        raw_text = request.body.decode("utf-8", errors="replace")
        return {
            "allowed": False,
            "rule_id": "malformed-body",
            "evidence": {
                "url": request.url,
                "body": raw_text[:80],
                "reason": str(exc),
                "matches": [{
                    "location": "body",
                    "original": raw_text[:120],
                    "normalized": canonical_text(raw_text)[:120],
                }],
            },
        }

    index = RuleIndex(rules)
    for rule in index.candidates(view.method, view.url.path):
        fragments = evaluate_rule(rule, view)
        if fragments is None:
            continue
        return {
            "allowed": rule.action != "deny",
            "rule_id": rule.rule_id,
            "evidence": {
                "url": request.url,
                "body": view.body.text[:80],
                "normalized": _normalized_evidence(view),
                "matches": fragments,
            },
        }
    return {
        "allowed": True,
        "rule_id": None,
        "evidence": {
            "url": request.url,
            "normalized": _normalized_evidence(view),
        },
    }
