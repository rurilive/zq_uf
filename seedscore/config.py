"""Scoring configuration and per-site overrides.

The public entry points accept a plain dict config so callers can keep
configuration in JSON/YAML.  ``resolve_config`` returns a fully populated
copy; the module-level ``DEFAULT_CONFIG`` is never mutated.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1",
    "threshold": 0.5,
    "weights": {
        "source_credibility": 0.35,
        "history_yield": 0.35,
        "link_quality": 0.30,
    },
    # source name -> credibility (number, 0..1 / 0..100, or label).
    "sources": {},
    # host or host suffix -> site rule.  Rule keys understood:
    #   threshold / min_score, weights, source_credibility, history_yield,
    #   link_quality, parts{...}, adjust{...}, bonus, penalty,
    #   blocked/deny/exclude, allow/allowed, dead/force_dead, known,
    #   sources{...}, version, name
    "sites": {},
    "dead_statuses": [
        "dead",
        "invalid",
        "expired",
        "gone",
        "removed",
        "blacklisted",
        "banned",
    ],
    "dead_http_statuses": [404, 410, 451],
    "dead_error_markers": [
        "404",
        "410",
        "not found",
        "gone",
        "nxdomain",
        "no such host",
        "dns fail",
        "connection refused",
    ],
    "tracking_params": [
        "gclid",
        "dclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "yclid",
        "spm",
        "scid",
        "cmpid",
        "_ga",
        "_gl",
        "ref",
        "ref_src",
        "referrer",
        "source",
        "campaign",
        "affiliate",
    ],
    "tracking_prefixes": ["utm_"],
    "session_params": [
        "sessionid",
        "session_id",
        "phpsessid",
        "jsessionid",
        "aspsessionid",
        "sid",
        "token",
        "auth",
        "password",
        "pwd",
        "email",
    ],
    "pagination_params": ["page", "p", "pg", "offset", "start", "page_num", "paged"],
    "search_params": ["q", "query", "s", "search", "keyword", "kw"],
    "spam_path_tokens": [
        "login",
        "signin",
        "signup",
        "signout",
        "logout",
        "register",
        "cart",
        "checkout",
        "admin",
        "wp-admin",
        "wp-login",
        "search",
        "print",
        "share",
        "redirect",
        "tracking",
        "unsubscribe",
        "password",
        "account",
        "ads",
        "adclick",
    ],
    "mild_path_tokens": ["tag", "author", "archive", "feed", "rss", "sitemap"],
    "binary_extensions": [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
        ".tiff", ".css", ".js", ".json", ".zip", ".gz", ".tar", ".rar",
        ".7z", ".exe", ".dmg", ".pkg", ".msi", ".apk", ".ipa", ".mp3",
        ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".woff", ".woff2",
        ".ttf", ".eot", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
        ".pptx",
    ],
    "feed_extensions": [".xml", ".rss", ".atom"],
    "document_extensions": [
        ".html", ".htm", ".shtml", ".php", ".asp", ".aspx", ".jsp", ".do",
    ],
    "suspicious_tlds": [
        "zip", "mov", "click", "download", "stream", "loan", "gdn", "review",
    ],
    "source_type_scores": {
        "manual": 0.95,
        "curated": 0.9,
        "trusted": 0.9,
        "sitemap": 0.8,
        "rss": 0.75,
        "feed": 0.75,
        "api": 0.75,
        "crawl": 0.55,
        "link": 0.5,
        "referral": 0.45,
        "external": 0.4,
        "unknown": 0.5,
    },
    "label_scores": {
        "high": 0.9,
        "trusted": 0.9,
        "good": 0.75,
        "medium": 0.6,
        "normal": 0.6,
        "unknown": 0.5,
        "low": 0.3,
        "bad": 0.15,
    },
    "history": {
        "prior_success": 1.0,
        "prior_failure": 1.0,
        "volume_scale": 50.0,
    },
}


def _deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge ``override`` on top of ``base``."""
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        result = {k: deepcopy(v) for k, v in base.items()}
        for key, value in override.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result
    if override is None:
        return deepcopy(base)
    return deepcopy(override)


def resolve_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return ``DEFAULT_CONFIG`` merged with a caller supplied config."""
    if not config:
        return deepcopy(DEFAULT_CONFIG)
    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")
    return _deep_merge(DEFAULT_CONFIG, config)


def _normalise_rule(rule: Any) -> dict[str, Any]:
    if rule is None:
        return {}
    if isinstance(rule, Mapping):
        return dict(rule)
    if isinstance(rule, bool):
        return {"blocked": not rule}
    if isinstance(rule, (int, float)):
        return {"threshold": float(rule)}
    if isinstance(rule, str):
        lowered = rule.strip().lower()
        if lowered in {"blocked", "deny", "reject", "off"}:
            return {"blocked": True}
        if lowered in {"dead", "invalid"}:
            return {"dead": True}
    return {}


def site_rule(config: Mapping[str, Any], host: str) -> tuple[str | None, dict[str, Any]]:
    """Return ``(matched_key, rule)`` for a host.

    Exact host matches win, then the longest matching suffix, then ``"*"``.
    Keys may be written with or without a leading dot (``.example.com`` and
    ``example.com`` are equivalent).
    """
    sites = config.get("sites") or {}
    if not isinstance(sites, Mapping):
        return None, {}
    host = (host or "").strip().lower().strip(".")
    best_key: str | None = None
    best_len = -1
    wildcard: dict[str, Any] = {}
    for raw_key, raw_rule in sites.items():
        key = str(raw_key).strip().lower()
        if key == "*":
            wildcard = _normalise_rule(raw_rule)
            continue
        # Both "example.com" and ".example.com" match the host and its
        # subdomains; "*.example.com" matches subdomains only.
        subdomains_only = key.startswith("*.")
        if subdomains_only:
            key = key[2:]
        key = key.lstrip(".")
        if not key:
            continue
        matched = host.endswith("." + key) if subdomains_only else (
            host == key or host.endswith("." + key)
        )
        if matched and len(key) > best_len:
            best_key = key
            best_len = len(key)
            best_rule = _normalise_rule(raw_rule)
    if best_key is not None:
        return best_key, dict(best_rule)
    if wildcard:
        return "*", wildcard
    return None, {}


def resolved_site_config(
    config: Mapping[str, Any] | None, host: str
) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """Resolve global config for a host.

    Returns ``(matched_site_key, site_rule, effective_config)`` where the
    effective config has site level weights/sources merged in.
    """
    cfg = resolve_config(config)
    key, rule = site_rule(cfg, host)
    effective = deepcopy(cfg)
    if rule:
        if isinstance(rule.get("weights"), Mapping):
            effective["weights"] = _deep_merge(cfg["weights"], rule["weights"])
        if isinstance(rule.get("sources"), Mapping):
            effective["sources"] = _deep_merge(cfg["sources"], rule["sources"])
        if "version" in rule:
            effective["version"] = rule["version"]
    return key, rule, effective
