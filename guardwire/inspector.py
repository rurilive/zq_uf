"""A tiny request inspector used by the gateway baseline."""
from dataclasses import dataclass, field
from typing import Dict, List


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


def inspect_request(request: Request, rules: List[Rule]) -> dict:
    body = request.body.decode("utf-8", errors="ignore")
    for rule in rules:
        if rule.method and rule.method.upper() != request.method.upper():
            continue
        if rule.path_prefix and not request.url.startswith(rule.path_prefix):
            continue
        if rule.needle and rule.needle not in request.url and rule.needle not in body:
            continue
        return {
            "allowed": rule.action != "deny",
            "rule_id": rule.rule_id,
            "evidence": {"url": request.url, "body": body[:80]},
        }
    return {"allowed": True, "rule_id": None, "evidence": {"url": request.url}}
