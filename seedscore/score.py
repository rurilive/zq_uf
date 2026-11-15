"""Seed scoring and admission filtering.

The scorer combines three observable signals:

``source_credibility``
    How trustworthy the referral source is.  Takes the highest precedence
    value from the seed itself, per-source configuration and the source-type
    table.

``history_yield``
    What the source/seed has produced before.  Uses a smoothed success rate,
    a volume confidence term and a freshness term.  No history is neutral
    (0.5), never a kill.

``link_quality``
    Features of the URL itself: HTTPS, depth, spam path/query tokens,
    binary/feed extensions, suspicious TLDs, etc.

A weighted sum produces ``score``.  ``filter`` additionally applies the
per-site threshold, collapses duplicate targets (keeping the best seed) and
separates dead seeds from the queue.  Already queued targets stay in their
existing order across re-scores; restorations bypass the threshold and are
appended after existing entries.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .config import resolve_config, resolved_site_config
from .urls import (
    host_of,
    is_ip_host,
    normalise_order,
    parsed_parts,
    query_pairs,
    site_of,
    target_key,
    try_normalise,
    url_extension,
    URLError,
)

COMPONENTS = ("source_credibility", "history_yield", "link_quality")
COMPONENT_LABELS = {
    "source_credibility": "来源可信度",
    "history_yield": "历史产出",
    "link_quality": "链接质量",
}

_DEFAULT_NEUTRAL = 0.5


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _unit(value: Any) -> float | None:
    """Parse a 0..1 or 0..100 number, returning a 0..1 float."""
    number = _number(value)
    if number is None:
        return None
    if 1.0 < number <= 100.0:
        number /= 100.0
    return _clamp(number)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _seed_dict(seed: Any) -> dict[str, Any]:
    if isinstance(seed, Mapping):
        return dict(seed)
    if isinstance(seed, str):
        return {"url": seed}
    return {"url": ""}


def _first(seed: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in seed and seed[key] is not None:
            return seed[key]
    return None


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 1e12:  # milliseconds
            number /= 1000.0
        try:
            result = datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    result = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# source credibility
# ---------------------------------------------------------------------------


def _source_lookup(sources: Mapping[str, Any], source: Any) -> Any:
    if not source:
        return None
    if source in sources:
        return sources[source]
    text = str(source).strip()
    if text in sources:
        return sources[text]
    lowered = text.lower()
    for key, value in sources.items():
        if str(key).strip().lower() == lowered:
            return value
    return None


def _parse_credibility(value: Any, config: Mapping[str, Any]) -> float | None:
    if isinstance(value, Mapping):
        for key in ("score", "credibility", "weight", "value", "trust"):
            if key in value:
                parsed = _parse_credibility(value[key], config)
                if parsed is not None:
                    return parsed
        stype = value.get("type") or value.get("source_type")
        if stype:
            table = config.get("source_type_scores") or {}
            return _unit(table.get(stype))
        return None
    unit = _unit(value)
    if unit is not None:
        return unit
    if isinstance(value, str):
        labels = config.get("label_scores") or {}
        return _unit(labels.get(value.strip().lower()))
    return None


def source_credibility(
    seed: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[float, list[str], Any]:
    """Return ``(score, notes, source_entry)`` for the source component."""
    neutral = _unit((config.get("history") or {}).get("neutral", _DEFAULT_NEUTRAL))
    neutral = _DEFAULT_NEUTRAL if neutral is None else neutral
    notes: list[str] = []

    for key in ("source_credibility", "source_score", "credibility", "trust"):
        if key in seed:
            value = _unit(seed[key])
            if value is not None:
                notes.append(f"seed.{key}={value:.2f}")
                return value, notes, None

    sources = config.get("sources") or {}
    source = _first(seed, ("source", "source_name", "from", "referrer", "origin"))
    entry = _source_lookup(sources, source) if source else None
    if entry is not None:
        value = _parse_credibility(entry, config)
        if value is not None:
            notes.append(f"source:{source}={value:.2f}")
            return value, notes, entry
        notes.append(f"source:{source}=unscored")

    stype = _first(seed, ("source_type", "sourceType", "type"))
    table = config.get("source_type_scores") or {}
    if stype is not None:
        value = _unit(table.get(str(stype).lower()))
        if value is not None:
            notes.append(f"source_type:{stype}={value:.2f}")
            return value, notes, entry

    if source is not None:
        value = _unit(table.get(str(source).lower()))
        if value is not None:
            notes.append(f"source_type:{source}={value:.2f}")
            return value, notes, entry

    value = _unit(seed.get("quality"))
    if value is not None:
        notes.append(f"seed.quality={value:.2f}")
        return value, notes, entry

    notes.append("source:unknown=neutral")
    return neutral, notes, entry


# ---------------------------------------------------------------------------
# history yield
# ---------------------------------------------------------------------------


def _history_data(seed: Mapping[str, Any], source_entry: Any) -> Mapping[str, Any]:
    history = seed.get("history")
    if not isinstance(history, Mapping):
        history = seed.get("history_stats")
    if not isinstance(history, Mapping):
        history = {}
    merged: dict[str, Any] = {}
    if isinstance(source_entry, Mapping):
        source_history = source_entry.get("history")
        if isinstance(source_history, Mapping):
            merged.update(source_history)
    merged.update(history)
    return merged


def _history_number(history: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    value = _first(history, keys)
    number = _number(value)
    if number is None:
        return None
    return max(0.0, number)


def history_yield(
    seed: Mapping[str, Any],
    config: Mapping[str, Any],
    source_entry: Any = None,
    now: datetime | None = None,
) -> tuple[float, list[str]]:
    """Return ``(score, notes)`` for the historical-yield component."""
    notes: list[str] = []
    hist_cfg = config.get("history") or {}
    neutral = _unit(hist_cfg.get("neutral", _DEFAULT_NEUTRAL))
    neutral = _DEFAULT_NEUTRAL if neutral is None else neutral

    for key in ("history_score", "yield_score", "history_yield"):
        if key in seed:
            value = _unit(seed[key])
            if value is not None:
                notes.append(f"seed.{key}={value:.2f}")
                return value, notes

    history = _history_data(seed, source_entry)
    explicit_rate = _unit(_first(history, ("success_rate", "rate", "ok_rate")))
    successes = _history_number(
        history, ("successes", "success", "ok", "ok_count", "success_count")
    )
    failures = _history_number(
        history, ("failures", "failure", "errors", "failed", "error_count")
    )
    attempts = _history_number(history, ("attempts", "fetches", "total", "total_count"))
    volume_units = _history_number(
        history, ("pages", "items", "yield", "pages_yielded", "documents", "records")
    )
    if successes is None and volume_units is not None:
        successes = volume_units
    if attempts is None:
        if successes is not None or failures is not None:
            attempts = (successes or 0.0) + (failures or 0.0)
    if volume_units is None:
        volume_units = successes

    last_success = _parse_time(
        _first(history, ("last_success", "last_success_at", "last_ok", "last_fetch"))
        or _first(seed, ("last_success", "last_success_at"))
    )
    last_failure = _parse_time(
        _first(history, ("last_failure", "last_failure_at", "last_error_at"))
    )

    has_data = any(
        value is not None
        for value in (explicit_rate, successes, failures, attempts, volume_units, last_success)
    )
    if not has_data:
        notes.append("history:none=neutral")
        return neutral, notes

    prior_success = _number(hist_cfg.get("prior_success")) or 1.0
    prior_failure = _number(hist_cfg.get("prior_failure")) or 1.0
    rate: float
    if explicit_rate is not None:
        rate = explicit_rate
        notes.append(f"rate={rate:.2f}(explicit)")
    elif attempts is not None and attempts > 0:
        success_part = successes if successes is not None else max(
            0.0, attempts - (failures or 0.0)
        )
        rate = (success_part + prior_success) / (attempts + prior_success + prior_failure)
        notes.append(
            f"rate={rate:.2f}({success_part:.0f}/{attempts:.0f}, smoothed)"
        )
    else:
        rate = neutral
        notes.append("rate=neutral")

    scale = _number(hist_cfg.get("volume_scale")) or 50.0
    if scale <= 0:
        scale = 50.0
    volume = volume_units or 0.0
    volume_conf = 1.0 - math.exp(-volume / scale) if volume > 0 else 0.0
    notes.append(f"volume={volume:.0f}->{volume_conf:.2f}")

    half_life = _number(hist_cfg.get("half_life_days")) or 180.0
    if half_life <= 0:
        half_life = 180.0
    if last_success is not None:
        age_days = max(0.0, (_now(now) - last_success).total_seconds() / 86400.0)
        freshness = 0.5 ** (age_days / half_life)
        notes.append(f"freshness={freshness:.2f}(age={age_days:.0f}d)")
    elif last_failure is not None:
        age_days = max(0.0, (_now(now) - last_failure).total_seconds() / 86400.0)
        freshness = 0.3 + 0.7 * (0.5 ** (age_days / half_life))
        notes.append(f"freshness={freshness:.2f}(last_failure)")
    else:
        freshness = 1.0

    value = 0.55 * rate + 0.25 * volume_conf + 0.20 * freshness
    value = _clamp(value)
    notes.append(f"history={value:.2f}")
    return value, notes


# ---------------------------------------------------------------------------
# link quality
# ---------------------------------------------------------------------------


def _path_depth(path: str) -> int:
    return len([part for part in path.split("/") if part]) if path not in {"", "/"} else 0


def link_quality(
    seed: Mapping[str, Any],
    url: str,
    config: Mapping[str, Any],
) -> tuple[float, list[str]]:
    """Return ``(score, notes)`` for URL-level quality features."""
    notes: list[str] = []
    if not url:
        return 0.0, ["url:invalid"]
    try:
        parts = parsed_parts(url)
    except (URLError, ValueError):
        return 0.0, ["url:invalid"]

    host = str(parts["host"])
    path = str(parts["path"])
    query = str(parts["query"])
    query_pairs_list = query_pairs(url)
    scheme = str(parts["scheme"])
    depth = _path_depth(path)
    hidden = _mapping(seed.get("link_features"))
    if "depth" in hidden:
        value = _number(hidden.get("depth"))
        if value is not None and value >= 0:
            depth = int(value)

    score = 0.55
    notes.append("base=0.55")

    if scheme == "https":
        score += 0.05
        notes.append("https=+0.05")
    else:
        score -= 0.05
        notes.append("http=-0.05")

    if depth == 0:
        score += 0.25
        notes.append("homepage=+0.25")
    elif depth == 1:
        score += 0.10
        notes.append("depth1=+0.10")
    elif depth == 2:
        pass
    elif depth == 3:
        score -= 0.05
        notes.append("depth3=-0.05")
    elif depth == 4:
        score -= 0.10
        notes.append("depth4=-0.10")
    else:
        score -= 0.18
        notes.append(f"depth{depth}=-0.18")

    tokens: list[str] = []
    for segment in path.split("/"):
        lowered = segment.lower().strip()
        if not lowered:
            continue
        tokens.extend(re.split(r"[^a-z0-9]+", lowered))
    tokens = [token for token in tokens if token]

    spam_tokens = {
        str(token).lower() for token in (config.get("spam_path_tokens") or [])
    }
    mild_tokens = {
        str(token).lower() for token in (config.get("mild_path_tokens") or [])
    }
    spam_hits = sorted({token for token in tokens if token in spam_tokens})
    mild_hits = sorted(
        {token for token in tokens if token in mild_tokens and token not in spam_tokens}
    )
    if spam_hits:
        penalty = min(0.30, 0.20 * len(spam_hits))
        score -= penalty
        notes.append("spam_path=" + ",".join(spam_hits) + f"=-{penalty:.2f}")
    if mild_hits:
        penalty = min(0.10, 0.05 * len(mild_hits))
        score -= penalty
        notes.append("mild_path=" + ",".join(mild_hits) + f"=-{penalty:.2f}")

    extension = url_extension(url)
    binary = {str(ext).lower() for ext in (config.get("binary_extensions") or [])}
    feeds = {str(ext).lower() for ext in (config.get("feed_extensions") or [])}
    documents = {str(ext).lower() for ext in (config.get("document_extensions") or [])}
    if extension in binary:
        score = min(score, 0.10)
        notes.append(f"binary_ext:{extension}=cap0.10")
    elif extension in feeds:
        score = min(score, 0.55)
        notes.append(f"feed_ext:{extension}=cap0.55")
    elif extension in documents:
        notes.append(f"document_ext:{extension}")

    if query_pairs_list:
        count = len(query_pairs_list)
        if count > 8:
            score -= 0.15
            notes.append(f"query_params={count}=-0.15")
        elif count > 4:
            score -= 0.08
            notes.append(f"query_params={count}=-0.08")
        else:
            notes.append(f"query_params={count}")
        keys = {key.lower() for key, _ in query_pairs_list}
        pagination = {
            str(token).lower() for token in (config.get("pagination_params") or [])
        }
        search = {str(token).lower() for token in (config.get("search_params") or [])}
        if keys & search:
            score -= 0.15
            notes.append("search_query=-0.15")
        if keys & pagination:
            score -= 0.08
            notes.append("pagination_query=-0.08")

    if len(url) > 300:
        score -= 0.12
        notes.append("url_too_long=-0.12")
    elif len(url) > 180:
        score -= 0.06
        notes.append("url_long=-0.06")

    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    suspicious = {
        str(token).lower() for token in (config.get("suspicious_tlds") or [])
    }
    if tld in suspicious:
        score -= 0.20
        notes.append(f"suspicious_tld:{tld}=-0.20")

    if is_ip_host(host):
        score -= 0.15
        notes.append("ip_host=-0.15")

    host_tokens = [token for token in re.split(r"[^a-z0-9]+", host) if token]
    spam_host = {
        token
        for token in host_tokens
        if token in {
            "casino", "betting", "porn", "xxx", "payday", "loan", "pharma",
            "viagra", "crypto", "gambling",
        }
    }
    if spam_host:
        score -= 0.25
        notes.append("spam_host=" + ",".join(sorted(spam_host)) + "=-0.25")

    # Pathological numeric/slug noise, e.g. /a/1/2/3/4/5.
    segments = [part for part in path.split("/") if part]
    if len(segments) >= 5 and all(part.isdigit() for part in segments[-4:]):
        score -= 0.06
        notes.append("numeric_path=-0.06")

    score = _clamp(score)
    notes.append(f"link={score:.2f}")
    return score, notes


# ---------------------------------------------------------------------------
# dead / blocked / new-site detection
# ---------------------------------------------------------------------------


def _status_text(seed: Mapping[str, Any]) -> str:
    value = _first(seed, ("status", "state", "seed_status"))
    if value is None:
        return ""
    return str(value).strip().lower()


def dead_reasons(
    seed: Mapping[str, Any],
    rule: Mapping[str, Any],
    config: Mapping[str, Any],
    now: datetime | None = None,
) -> list[str]:
    """Return human-readable reasons the seed is dead (empty means alive)."""
    reasons: list[str] = []
    if rule.get("dead") or rule.get("force_dead"):
        reasons.append("site_rule")

    if seed.get("dead") is True or seed.get("is_dead") is True:
        reasons.append("marked_dead")
    if seed.get("alive") is False or seed.get("active") is False:
        reasons.append("marked_inactive")

    status = _status_text(seed)
    dead_statuses = {
        str(token).lower() for token in (config.get("dead_statuses") or [])
    }
    if status and status in dead_statuses:
        reasons.append(f"status:{status}")

    http_status = _first(
        seed, ("http_status", "status_code", "http_code", "response_code", "code")
    )
    number = _number(http_status)
    if number is not None:
        codes = {
            int(code)
            for code in (config.get("dead_http_statuses") or [])
            if _number(code) is not None
        }
        if int(number) in codes:
            reasons.append(f"http:{int(number)}")

    error = _first(seed, ("error", "last_error", "failure_reason", "error_message"))
    if error:
        text = str(error).lower()
        for marker in config.get("dead_error_markers") or []:
            marker_text = str(marker).lower()
            if marker_text and marker_text in text:
                reasons.append(f"error:{marker_text}")
                break

    for key in ("expires_at", "expires", "expire_at", "expired_at", "valid_until"):
        if key in seed:
            when = _parse_time(seed[key])
            if when is not None and when <= _now(now):
                reasons.append(f"expired:{key}")
                break

    # Deduplicate while preserving order.
    result: list[str] = []
    for reason in reasons:
        if reason not in result:
            result.append(reason)
    return result


def is_blocked(seed: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    if seed.get("blocked") is True or seed.get("deny") is True:
        return True
    if rule.get("blocked") or rule.get("deny") or rule.get("exclude"):
        return True
    if rule.get("allow") is False or rule.get("allowed") is False:
        return True
    return False


def is_new_site(
    seed: Mapping[str, Any] | str,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Best-effort classification of a seed as a previously unseen site.

    An explicit ``is_new``/``new_site``/``known_site`` flag wins.  Otherwise a
    seed with no usable history is considered new.  Callers with a site
    inventory should pass the flag explicitly.
    """
    seed = _seed_dict(seed)
    for key in ("is_new", "new_site"):
        if key in seed:
            return bool(seed[key])
    if "known_site" in seed or "site_known" in seed:
        return not bool(seed.get("known_site", seed.get("site_known")))
    history = _history_data(seed, None)
    if history:
        attempts = _history_number(
            history, ("attempts", "fetches", "total", "total_count")
        )
        successes = _history_number(history, ("successes", "success", "ok"))
        failures = _history_number(history, ("failures", "failure", "errors"))
        if any(value for value in (attempts, successes, failures)):
            return False
    cfg = resolve_config(config) if config else {}
    sources = cfg.get("sources") or {}
    host = ""
    try:
        host = host_of(str(seed.get("url") or ""))
    except (URLError, ValueError):
        host = ""
    site = site_of(host)
    sites = cfg.get("sites") or {}
    for key in (host, site):
        rule = sites.get(key)
        if isinstance(rule, Mapping) and rule.get("known") is True:
            return False
    for key in (host, site):
        if key and _source_lookup(sources, key) is not None:
            return False
    return True


# ---------------------------------------------------------------------------
# public scoring
# ---------------------------------------------------------------------------


def _site_adjustments(
    rule: Mapping[str, Any],
    seed: Mapping[str, Any],
    parts: dict[str, float],
    notes: list[str],
) -> None:
    fixed = _mapping(rule.get("parts"))
    for component in COMPONENTS:
        if component in fixed:
            value = _unit(fixed[component])
            if value is not None:
                parts[component] = value
                notes.append(f"site.parts.{component}={value:.2f}")
        if component in rule:
            value = _unit(rule.get(component))
            if value is not None:
                parts[component] = value
                notes.append(f"site.{component}={value:.2f}")
    adjust = _mapping(rule.get("adjust"))
    for component in COMPONENTS:
        delta = _number(adjust.get(component))
        if delta is not None:
            parts[component] = _clamp(parts[component] + delta)
            notes.append(f"site.adjust.{component}={delta:+.2f}")


def _weights(config: Mapping[str, Any]) -> dict[str, float]:
    raw = config.get("weights") or {}
    values: dict[str, float] = {}
    for component in COMPONENTS:
        value = _number(raw.get(component))
        values[component] = max(0.0, value) if value is not None else 0.0
    total = sum(values.values())
    if total <= 0:
        return {component: 1.0 / len(COMPONENTS) for component in COMPONENTS}
    return {component: values[component] / total for component in COMPONENTS}


def _global_threshold(config: Mapping[str, Any]) -> float:
    value = _unit(config.get("threshold"))
    return 0.5 if value is None else value


def score(
    seed: Mapping[str, Any] | str,
    config: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score one seed and explain the result.

    The returned mapping contains ``url``, ``target``, ``site``, ``score``,
    ``parts`` (the three raw components), ``weights``, ``contributions``,
    ``threshold``, ``passes_threshold``, ``dead``/``dead_reasons``,
    ``blocked``, ``new_site`` and ``notes``.  Invalid URLs score 0 with
    ``reason == "invalid_url"`` rather than raising.
    """
    if not isinstance(seed, Mapping):
        seed = _seed_dict(seed)
    raw_value = seed.get("url") or seed.get("link") or seed.get("href") or ""
    raw_url = raw_value if isinstance(raw_value, str) else ""
    cfg = resolve_config(config)
    base: dict[str, Any] = {
        "url": raw_url,
        "original_url": raw_url,
        "target": "",
        "host": "",
        "site": "",
        "score": 0.0,
        "parts": {component: 0.0 for component in COMPONENTS},
        "weights": _weights(cfg),
        "contributions": {component: 0.0 for component in COMPONENTS},
        "threshold": _global_threshold(cfg),
        "passes_threshold": False,
        "dead": False,
        "dead_reasons": [],
        "blocked": False,
        "restored": False,
        "new_site": False,
        "reason": "",
        "notes": [],
        "config_version": cfg.get("version"),
        "site_rule": None,
    }

    normalised, error = try_normalise(
        raw_url,
        tracking_params=cfg.get("tracking_params"),
        tracking_prefixes=cfg.get("tracking_prefixes"),
        session_params=cfg.get("session_params"),
    )
    if error or not normalised:
        base["reason"] = "invalid_url"
        base["notes"] = [f"url:invalid:{error}"]
        return base

    host = host_of(normalised)
    site_key, rule, effective = resolved_site_config(cfg, host)
    base["url"] = normalised
    base["host"] = host
    base["site"] = site_of(host)
    base["target"] = target_key(normalised)
    base["site_rule"] = site_key
    base["config_version"] = effective.get("version")

    notes: list[str] = []

    source_value, source_notes, source_entry = source_credibility(seed, effective)
    history_value, history_notes = history_yield(
        seed, effective, source_entry, now=now
    )
    link_value, link_notes = link_quality(seed, normalised, effective)
    notes.extend(source_notes)
    notes.extend(history_notes)
    notes.extend(link_notes)

    parts = {
        "source_credibility": source_value,
        "history_yield": history_value,
        "link_quality": link_value,
    }
    _site_adjustments(rule, seed, parts, notes)

    weights = _weights(effective)
    contributions = {
        component: weights[component] * parts[component] for component in COMPONENTS
    }
    total = sum(contributions.values())
    bonus = _number(rule.get("bonus")) or 0.0
    penalty = _number(rule.get("penalty")) or 0.0
    if bonus:
        notes.append(f"site.bonus={bonus:+.2f}")
    if penalty:
        notes.append(f"site.penalty={-abs(penalty):+.2f}")
    total = _clamp(total + bonus - abs(penalty))

    threshold = rule.get("threshold", rule.get("min_score"))
    threshold_value = _unit(threshold)
    if threshold_value is None:
        threshold_value = _global_threshold(effective)
    else:
        notes.append(f"site.threshold={threshold_value:.2f}")

    dead = dead_reasons(seed, rule, effective, now=now)
    blocked = is_blocked(seed, rule)

    base.update(
        {
            "score": round(total, 6),
            "parts": {key: round(value, 6) for key, value in parts.items()},
            "weights": {key: round(value, 6) for key, value in weights.items()},
            "contributions": {
                key: round(value, 6) for key, value in contributions.items()
            },
            "threshold": threshold_value,
            "passes_threshold": total >= threshold_value,
            "dead": bool(dead),
            "dead_reasons": dead,
            "blocked": blocked,
            "new_site": is_new_site(seed, cfg),
            "notes": notes,
        }
    )
    if dead:
        base["reason"] = "dead"
    elif blocked:
        base["reason"] = "blocked"
    elif not base["passes_threshold"]:
        base["reason"] = "low_score"
    else:
        base["reason"] = "ok"
    return base


# ---------------------------------------------------------------------------
# admission filter
# ---------------------------------------------------------------------------


def _review_state(
    restored: Iterable[Any] | None,
    review: Any,
    review_at: datetime | None,
    config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return ``target -> restore record`` and the restore order."""
    cfg = config or {}
    state: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def canonical(raw: str) -> str:
        normalised, error = try_normalise(
            raw,
            tracking_params=cfg.get("tracking_params"),
            tracking_prefixes=cfg.get("tracking_prefixes"),
            session_params=cfg.get("session_params"),
        )
        if error or not normalised:
            return raw
        return target_key(normalised)

    def add(item: Any, record: Mapping[str, Any] | None = None) -> None:
        if isinstance(item, Mapping):
            raw = item.get("target") or item.get("url") or ""
            payload = dict(record or {})
            payload.update(item)
        else:
            raw = str(item or "")
            payload = dict(record or {})
        if not raw:
            return
        # A target key is already canonical; a URL must be normalised.
        target = str(raw)
        if "://" in target or target.startswith("//"):
            target = canonical(target)
        if target not in order:
            order.append(target)
        state[target] = payload

    if review is not None:
        if hasattr(review, "restored_map"):
            records = review.restored_map(at=review_at)
            for target, record in records.items():
                add(target, record)
        elif hasattr(review, "restored_targets"):
            for item in review.restored_targets(at=review_at):
                add(item)
        elif isinstance(review, Mapping):
            for target, record in review.items():
                add(target, record if isinstance(record, Mapping) else None)
        else:
            for item in review:
                add(item)
    if restored is not None:
        for item in restored:
            add(item)
    return state, order


def _record_from_score(seed: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(seed)
    record.update({key: value for key, value in result.items() if key != "notes"})
    record["notes"] = list(result.get("notes") or [])
    return record


def _target_key_of(record: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """Recover the canonical target for a report record."""
    target = record.get("target")
    if target:
        return str(target)
    raw = str(record.get("url") or "")
    normalised, error = try_normalise(
        raw,
        tracking_params=config.get("tracking_params"),
        tracking_prefixes=config.get("tracking_prefixes"),
        session_params=config.get("session_params"),
    )
    if error or not normalised:
        return raw
    return target_key(normalised)


def _reason_counts(reasons: Iterable[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {"reason": reason, "count": counts[reason]}
        for reason in sorted(counts, key=lambda key: (-counts[key], key))
    ]


def filter(
    seeds: Iterable[Mapping[str, Any] | str],
    config: Mapping[str, Any] | None = None,
    *,
    limit: int | None = None,
    queue: Iterable[Any] | None = None,
    restored: Iterable[Any] | None = None,
    review: Any = None,
    review_at: datetime | None = None,
    previous: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score and admit seeds, returning kept/dropped/dead inventories.

    ``queue`` is the current admitted order (target keys, URLs or seed
    mappings).  Matching targets stay first and keep their relative order, even
    if a new score now falls below the threshold -- re-scoring must not reshuffle
    a live queue.  Dead targets are removed from the queue and reported in
    ``dead``/``queue_removed``.

    ``restored`` (or a :class:`~seedscore.review.ReviewLog` passed as
    ``review``) admits a wrongly killed target regardless of threshold and
    appends it after existing queue entries.  ``force`` restore records may
    re-admit a dead target, which remains flagged as dead.
    """
    cfg = resolve_config(config)
    seed_list = list(seeds or [])
    queue_keys = normalise_order(queue, config=cfg)
    restore_state, restore_order = _review_state(restored, review, review_at, cfg)

    scored: list[dict[str, Any]] = []
    for index, raw_seed in enumerate(seed_list):
        seed = _seed_dict(raw_seed)
        result = score(seed, cfg, now=now)
        record = _record_from_score(seed, result)
        record["_index"] = index
        record["_target"] = result.get("target") or ""
        scored.append(record)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    dead: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    queue_removed: list[dict[str, Any]] = []

    # Group by target.  Invalid seeds cannot be grouped and are dropped.
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in scored:
        target = record["_target"]
        if not target:
            record["status"] = "dropped"
            record["reason"] = "invalid_url"
            dropped.append(record)
            continue
        groups.setdefault(target, []).append(record)

    queue_set = set(queue_keys)

    def rank(record: Mapping[str, Any]) -> tuple[float, float, int]:
        target = record["_target"]
        # Queue membership first for stability, then score, then input order.
        return (
            1.0 if target in queue_set else 0.0,
            float(record.get("score") or 0.0),
            -int(record.get("_index", 0)),
        )

    selected: list[dict[str, Any]] = []
    for target, candidates in groups.items():
        alive = [item for item in candidates if not item.get("dead")]
        dead_items = [item for item in candidates if item.get("dead")]
        for item in dead_items:
            item["status"] = "dead"
            item["reason"] = "dead"
            restore_record = restore_state.get(target)
            # A live variant of the same target wins: the restore stays
            # attached so the surviving seed is still marked restored.
            if (
                restore_record is not None
                and restore_record.get("force")
                and not alive
            ):
                item["restored"] = True
                item["restored_dead"] = True
                item["status"] = "kept"
                item["admission"] = "restored"
                item["restore_record"] = {
                    key: restore_record.get(key)
                    for key in ("id", "actor", "reason", "at", "force")
                    if key in restore_record
                }
                selected.append(item)
                continue
            dead.append(item)
            # Only report the target as removed from the queue when no live
            # variant of the same target keeps it there.
            if target in queue_set and not alive:
                item["removed_from_queue"] = True
                queue_removed.append(item)
        if not alive:
            continue
        alive.sort(key=rank, reverse=True)
        chosen = alive[0]
        for item in alive[1:]:
            item["status"] = "dropped"
            item["reason"] = "duplicate"
            item["duplicate_of"] = target
            item["kept_score"] = chosen.get("score")
            duplicates.append(item)
            dropped.append(item)

        restore_record = restore_state.get(target)
        has_restore = restore_record is not None
        chosen["restored"] = has_restore
        if has_restore:
            chosen["restore_record"] = {
                key: restore_record.get(key)
                for key in ("id", "actor", "reason", "at", "force")
                if key in restore_record
            }
        if target in queue_set:
            chosen["status"] = "kept"
            chosen["admission"] = "existing"
            chosen["retained"] = True
            if not chosen.get("passes_threshold") and not has_restore:
                chosen.setdefault("notes", []).append("existing_queue:below_threshold_retained")
            selected.append(chosen)
        elif chosen.get("blocked") and not has_restore:
            chosen["status"] = "dropped"
            chosen["reason"] = "blocked"
            dropped.append(chosen)
        elif not chosen.get("passes_threshold") and not has_restore:
            chosen["status"] = "dropped"
            chosen["reason"] = "low_score"
            dropped.append(chosen)
        else:
            chosen["status"] = "kept"
            chosen["admission"] = "restored" if has_restore else "new"
            selected.append(chosen)

    # Order: existing queue, then restored, then new by score.
    by_target = {record["_target"]: record for record in selected}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in queue_keys:
        record = by_target.get(target)
        if record is not None and target not in seen:
            ordered.append(record)
            seen.add(target)
    for target in restore_order:
        record = by_target.get(target)
        if record is not None and target not in seen:
            ordered.append(record)
            seen.add(target)
    new_records = [
        record
        for record in selected
        if record["_target"] not in seen
    ]
    new_records.sort(
        key=lambda record: (-float(record.get("score") or 0.0), int(record.get("_index", 0)))
    )
    ordered.extend(new_records)

    # Capacity applies to newly admitted seeds; existing queue entries and
    # manual restores are never evicted by a re-score or by the quota.
    limited: list[dict[str, Any]] = []
    if limit is not None:
        try:
            capacity = max(0, int(limit))
        except (TypeError, ValueError):
            capacity = 0
        protected = sum(
            1
            for record in ordered
            if record.get("admission") == "existing"
            or record.get("admission") == "restored"
            or record.get("restored_dead")
        )
        allowed_new = max(0, capacity - protected)
        new_used = 0
        for record in ordered:
            if (
                record.get("admission") in {"existing", "restored"}
                or record.get("restored_dead")
            ):
                limited.append(record)
                continue
            if new_used < allowed_new:
                limited.append(record)
                new_used += 1
            else:
                record["status"] = "dropped"
                record["reason"] = "limit"
                dropped.append(record)
        ordered = limited

    for position, record in enumerate(ordered):
        record["position"] = position
        record.pop("_index", None)
        record.pop("_target", None)
    for record in dropped:
        record.pop("_index", None)
        record.pop("_target", None)
    for record in dead:
        record.pop("_index", None)
        record.pop("_target", None)

    # Keep kept ordering stable and deterministic.
    ordered.sort(key=lambda record: record.get("position", 0))

    # The queue is stable iff every surviving queued target appears in its
    # original relative order.  (Newly restored targets are appended.)
    kept_order = [
        _target_key_of(record, cfg) for record in ordered
        if record.get("admission") in {"existing", "restored"} or record.get("restored_dead")
    ]
    kept_targets = set(kept_order)
    expected = [target for target in queue_keys if target in kept_targets]
    queue_stable = [target for target in kept_order if target in set(queue_keys)] == expected

    reasons = _reason_counts(record.get("reason", "unknown") for record in dropped)
    dead_reason_counts = _reason_counts(
        reason for record in dead for reason in (record.get("dead_reasons") or ["dead"])
    )
    by_site: dict[str, dict[str, Any]] = {}
    for status, records in (("kept", ordered), ("dropped", dropped), ("dead", dead)):
        for record in records:
            site = record.get("site") or "(unknown)"
            bucket = by_site.setdefault(
                site,
                {
                    "site": site,
                    "kept": 0,
                    "dropped": 0,
                    "dead": 0,
                    "duplicates": 0,
                },
            )
            bucket[status] += 1
            if record.get("reason") == "duplicate":
                bucket["duplicates"] += 1

    report: dict[str, Any] = {
        "kept": ordered,
        "dropped": dropped,
        "dead": dead,
        "duplicates": duplicates,
        "reasons": reasons,
        "dead_reasons": dead_reason_counts,
        "queue_removed": queue_removed,
        "summary": {
            "input": len(scored),
            "kept": len(ordered),
            "new": sum(1 for record in ordered if record.get("admission") == "new"),
            "existing": sum(
                1 for record in ordered if record.get("admission") == "existing"
            ),
            "restored": sum(
                1 for record in ordered
                if record.get("admission") == "restored" or record.get("restored_dead")
            ),
            "dropped": len(dropped),
            "dead": len(dead),
            "duplicates": len(duplicates),
            "below_threshold": sum(
                1 for record in dropped if record.get("reason") == "low_score"
            ),
            "blocked": sum(1 for record in dropped if record.get("reason") == "blocked"),
            "invalid": sum(
                1 for record in dropped if record.get("reason") == "invalid_url"
            ),
            "limited": sum(1 for record in dropped if record.get("reason") == "limit"),
            "queue_removed": len(queue_removed),
        },
        "by_site": by_site,
        "threshold": _global_threshold(cfg),
        "queue": list(queue_keys),
        "queue_stable": queue_stable,
        "restored_targets": list(restore_order),
        "config_version": cfg.get("version"),
    }
    if previous is not None:
        # Imported lazily: shadow.py scores through this module.
        from .shadow import diff_reports

        report["changes"] = diff_reports(previous, report)
    return report


__all__ = [
    "COMPONENTS",
    "COMPONENT_LABELS",
    "dead_reasons",
    "filter",
    "history_yield",
    "is_blocked",
    "is_new_site",
    "link_quality",
    "score",
    "source_credibility",
]
