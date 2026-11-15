"""Content-based link fingerprints, alias tracking and adaptive re-visits.

The public surface stays ``fingerprint(url)`` and ``due(url, now)``; everything
else is additive.  A :class:`LinkStore` owns the state so several crawlers can
keep isolated queues, while the module-level functions use a default store.
"""
from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .normalize import NormalizeRules, normalize

BASE_INTERVAL = 3600
MIN_INTERVAL = 60
MAX_FACTOR = 8.0
MIN_FACTOR = 0.25
CHANGE_ALPHA = 0.5

MODES = ("legacy", "trial", "active")

# Scheduling rule ids returned in the result payload.
RULE_NEW = "new-target"
RULE_ALIAS = "alias"
RULE_WAIT = "interval-wait"
RULE_ELAPSED = "interval-elapsed"
RULE_HOT_WAIT = "hot-interval-wait"
RULE_HOT_ELAPSED = "hot-interval-elapsed"
RULE_COLD_WAIT = "cold-interval-wait"
RULE_COLD_ELAPSED = "cold-interval-elapsed"


def legacy_fingerprint(url):
    """The pre-existing behaviour: hash the whole raw string."""
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:16]


def _utc_day(now) -> str:
    try:
        stamp = float(now)
    except (TypeError, ValueError):
        return "unknown"
    return datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Alias:
    raw: str
    first_seen: float
    hits: int = 0


@dataclass
class _Raw:
    raw: str
    site: str
    first_seen: float


@dataclass(eq=False)
class Entry:
    """One content target, keyed by its normalised fingerprint."""

    site: str
    fingerprint: str
    canonical_raw: str
    normalized_url: str
    first_seen: float
    last_fetch: float | None = None
    fetches: int = 0
    change_rate: float | None = None
    observations: int = 0
    aliases: dict[str, Alias] = field(default_factory=dict)


class LinkStore:
    """Per-store link queue with alias folding and adaptive intervals."""

    def __init__(
        self,
        base_interval: float = BASE_INTERVAL,
        rules: NormalizeRules | None = None,
        default_mode: str = "active",
        alias_ratio_threshold: float = 0.5,
        alias_min_count: int = 5,
    ):
        if default_mode not in MODES:
            raise ValueError(f"unknown mode: {default_mode!r}")
        self.base_interval = float(base_interval)
        self.rules = rules or NormalizeRules()
        self.default_mode = default_mode
        self.alias_ratio_threshold = alias_ratio_threshold
        self.alias_min_count = alias_min_count

        self._entries: dict[tuple[str, str], Entry] = {}
        self._raws: dict[tuple[str, str], _Raw] = {}
        self._modes: dict[str, str] = {}
        self._daily: dict[str, dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        # Simulation of the old whole-URL buckets, for trials.
        self._legacy_shadow: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------ rules
    def set_rules(self, rules: NormalizeRules) -> None:
        """Install new normalisation rules (call :meth:`rejudge` to apply)."""
        self.rules = rules

    def _normalize(self, url):
        return normalize(url, self.rules)

    def _canonical_url(self, raw: str, site: str) -> str:
        """Normalised URL for display; legacy sites keep their raw address."""
        if self._mode_for(site) == "legacy":
            return raw
        return self._normalize(raw).url

    def fingerprint(self, url) -> str:
        """Fingerprint of the content target *url* points at."""
        raw = "" if url is None else str(url)
        norm = self._normalize(raw)
        if self._mode_for(norm.site) == "legacy":
            return legacy_fingerprint(raw)
        return norm.fingerprint

    # ------------------------------------------------------- per-site rollout
    def _mode_for(self, site: str) -> str:
        return self._modes.get(site, self.default_mode)

    def site_mode(self, site: str) -> str:
        return self._mode_for(site)

    def start_trial(self, site: str) -> dict:
        """Run the new rules on one site while comparing against the old ones."""
        self._modes[site] = "trial"
        return self.rejudge()

    def promote(self, site: str) -> None:
        """Keep the new rules for *site* after a trial looked good."""
        self._modes[site] = "active"

    def rollback(self, site: str) -> dict:
        """Return *site* to whole-URL behaviour, leaving other sites alone."""
        self._modes[site] = "legacy"
        return self.rejudge()

    # --------------------------------------------------------------- decision
    def due(self, url, now) -> dict:
        raw = "" if url is None else str(url)
        norm = self._normalize(raw)
        site = norm.site
        mode = self._mode_for(site)
        now = float(now)
        metrics = self._daily[site][_utc_day(now)]

        if mode == "legacy":
            fp = legacy_fingerprint(raw)
            key = (site, fp)
            entry = self._entries.get(key)
            if entry is None:
                entry = Entry(
                    site=site,
                    fingerprint=fp,
                    canonical_raw=raw,
                    normalized_url=raw,
                    first_seen=now,
                )
                self._entries[key] = entry
                self._raws[(site, raw)] = _Raw(raw, site, now)
                metrics["links_seen"] += 1
                metrics["new_targets"] += 1
            result = self._decide(entry, raw, norm, now, mode, metrics)
            result["normalization"] = list(norm.rules)
            return result

        fp = norm.fingerprint
        if (site, raw) not in self._raws:
            self._raws[(site, raw)] = _Raw(raw, site, now)
            metrics["links_seen"] += 1

        key = (site, fp)
        entry = self._entries.get(key)
        if entry is None:
            entry = Entry(
                site=site,
                fingerprint=fp,
                canonical_raw=raw,
                normalized_url=norm.url,
                first_seen=now,
            )
            self._entries[key] = entry
            metrics["new_targets"] += 1
            result = self._decide(
                entry, raw, norm, now, mode, metrics, reason="new", matched=RULE_NEW
            )
        else:
            alias = raw != entry.canonical_raw
            reason = "alias" if alias else "interval"
            if now < entry.first_seen and alias:
                # Out-of-order arrival: the spelling just seen was queued
                # before the target's canonical one, so it takes over.
                previous = entry.canonical_raw
                entry.aliases[previous] = Alias(
                    raw=previous, first_seen=entry.first_seen
                )
                prior = entry.aliases.pop(raw, None)
                entry.canonical_raw = raw
                entry.normalized_url = norm.url
                entry.first_seen = now
                alias = False
                reason = "earlier-spelling"
                metrics["alias_hits"] += 1
                if prior is None:
                    metrics["aliases"] += 1
            elif alias:
                metrics["alias_hits"] += 1
                if raw in entry.aliases:
                    entry.aliases[raw].hits += 1
                    entry.aliases[raw].first_seen = min(
                        entry.aliases[raw].first_seen, now
                    )
                else:
                    entry.aliases[raw] = Alias(raw=raw, first_seen=now, hits=1)
                    metrics["aliases"] += 1
            elif now < entry.first_seen:
                entry.first_seen = now
            result = self._decide(
                entry, raw, norm, now, mode, metrics,
                alias=alias,
                reason=reason,
            )

        if mode == "trial":
            self._trial_compare(site, raw, now, result, metrics)
        return result

    def _decide(self, entry, raw, norm, now, mode, metrics, alias=False,
                reason=None, matched=None) -> dict:
        interval, factor = self._interval(entry)
        if entry.last_fetch is None:
            is_due = True
            matched = RULE_NEW if not alias else RULE_ALIAS
            reason = reason or "new"
        else:
            elapsed = now - entry.last_fetch
            is_due = elapsed >= interval
            matched = matched or self._rule_for(factor, is_due)

        if is_due:
            entry.last_fetch = now
            entry.fetches += 1
            metrics["due"] += 1
            metrics["fetches"] += 1
        elif alias:
            # Same target, new spelling, still inside its interval: the old
            # whole-URL behaviour would have fetched it again.
            metrics["duplicate_fetches"] += 1

        return {
            "url": raw,
            "address": entry.canonical_raw,
            "canonical_url": entry.normalized_url,
            "fingerprint": entry.fingerprint,
            "site": entry.site,
            "due": is_due,
            "interval": round(interval, 3),
            "interval_factor": round(factor, 4),
            "change_rate": (
                None if entry.change_rate is None else round(entry.change_rate, 4)
            ),
            "reason": reason or "interval",
            "matched_rule": matched,
            "rule": matched,
            "alias": alias,
            "alias_of": entry.canonical_raw if alias else None,
            "mode": mode,
            "normalization": list(norm.rules),
            "explanation": self._explain(
                entry, norm, interval, factor, is_due, matched, alias, mode
            ),
        }

    def _rule_for(self, factor: float, is_due: bool) -> str:
        if factor < 0.95:
            return RULE_HOT_ELAPSED if is_due else RULE_HOT_WAIT
        if factor > 1.05:
            return RULE_COLD_ELAPSED if is_due else RULE_COLD_WAIT
        return RULE_ELAPSED if is_due else RULE_WAIT

    def _interval(self, entry: Entry) -> tuple[float, float]:
        if entry.change_rate is None:
            return self.base_interval, 1.0
        rate = _clamp(entry.change_rate, 0.0, 1.0)
        factor = _clamp(2.0 ** (1.0 - 2.0 * rate), MIN_FACTOR, MAX_FACTOR)
        interval = _clamp(
            self.base_interval * factor, MIN_INTERVAL, self.base_interval * MAX_FACTOR
        )
        return interval, factor

    def _explain(self, entry, norm, interval, factor, is_due, matched,
                 alias, mode) -> str:
        pieces = []
        if mode == "legacy":
            pieces.append("site rolled back to whole-URL hashing")
        if matched == RULE_NEW:
            pieces.append("first time this target is queued")
        elif matched == RULE_ALIAS:
            pieces.append("new spelling of a target already queued")
        else:
            pieces.append(
                f"{'due' if is_due else 'not due'} after {round(interval)}s interval"
            )
        if alias:
            pieces.append(f"resolves to {entry.canonical_raw!r}")
        if norm.rules:
            pieces.append("normalisation: " + ", ".join(norm.rules))
        if entry.change_rate is not None:
            trend = "hot" if factor < 1.0 else "cold" if factor > 1.0 else "steady"
            pieces.append(
                f"{trend} page (change_rate={entry.change_rate:.2f}, "
                f"interval x{factor:.2f})"
            )
        if mode == "trial":
            pieces.append("site on trial; old behaviour compared alongside")
        return "; ".join(pieces)

    def _trial_compare(self, site, raw, now, result, metrics) -> None:
        key = (site, legacy_fingerprint(raw))
        last = self._legacy_shadow.get(key)
        legacy_due = last is None or (now - last) >= self.base_interval
        if legacy_due:
            self._legacy_shadow[key] = now
            metrics["legacy_due"] += 1
        if legacy_due and not result["due"]:
            metrics["avoided_duplicates"] += 1

    # ------------------------------------------------------ update frequency
    def observe(self, url, now, changed: bool) -> dict:
        """Record whether a fetch found new content and reschedule the target."""
        raw = "" if url is None else str(url)
        norm = self._normalize(raw)
        site = norm.site
        mode = self._mode_for(site)
        fp = legacy_fingerprint(raw) if mode == "legacy" else norm.fingerprint
        entry = self._entries.get((site, fp))
        if entry is None:
            return {"url": raw, "found": False, "fingerprint": fp}

        value = 1.0 if changed else 0.0
        if entry.change_rate is None:
            entry.change_rate = 0.5
        entry.change_rate = entry.change_rate * (1 - CHANGE_ALPHA) + CHANGE_ALPHA * value
        entry.change_rate = _clamp(entry.change_rate, 0.0, 1.0)
        entry.observations += 1
        metrics = self._daily[site][_utc_day(now)]
        metrics["observations"] += 1
        if changed:
            metrics["content_changes"] += 1

        interval, factor = self._interval(entry)
        return {
            "url": raw,
            "fingerprint": entry.fingerprint,
            "changed": bool(changed),
            "change_rate": round(entry.change_rate, 4),
            "interval": round(interval, 3),
            "interval_factor": round(factor, 4),
        }

    # ------------------------------------------------------------- aliases
    def alias_report(self, site: str | None = None) -> list[dict]:
        """Alias pressure for one site or all sites, worst first."""
        report = []
        for entry in self._entries.values():
            if site is not None and entry.site != site:
                continue
            if not entry.aliases:
                continue
            total = len(entry.aliases) + 1
            report.append(
                {
                    "site": entry.site,
                    "canonical_url": entry.normalized_url,
                    "address": entry.canonical_raw,
                    "fingerprint": entry.fingerprint,
                    "alias_count": len(entry.aliases),
                    "total_spellings": total,
                    "alias_ratio": round(len(entry.aliases) / total, 4),
                    "samples": [a.raw for a in entry.aliases.values()][:5],
                }
            )
        report.sort(key=lambda item: item["alias_count"], reverse=True)
        return report

    def alerts(self, site: str | None = None) -> list[dict]:
        """Targets whose alias load crosses the configured thresholds."""
        alerts = []
        for item in self.alias_report(site):
            if (
                item["alias_count"] >= self.alias_min_count
                or item["alias_ratio"] >= self.alias_ratio_threshold
            ):
                alerts.append({"level": "warning", **item})
        return alerts

    def site_alerts(self, site: str | None = None) -> list[dict]:
        """Daily per-site alias ratios that exceed the threshold."""
        alerts = []
        for name, days in self.daily_metrics(site).items():
            for day, row in days.items():
                ratio = row["alias_ratio"]
                if day != "unknown" and ratio >= self.alias_ratio_threshold:
                    alerts.append(
                        {
                            "level": "warning",
                            "site": name,
                            "day": day,
                            "alias_ratio": ratio,
                            "aliases": row["aliases"],
                            "links_seen": row["links_seen"],
                        }
                    )
        alerts.sort(key=lambda item: (item["alias_ratio"], item["day"]), reverse=True)
        return alerts

    def export_aliases(self, path=None, site: str | None = None,
                       limit: int | None = None) -> list[dict]:
        """Return alias samples and optionally write them as CSV."""
        rows = self.alias_report(site)
        if limit is not None:
            rows = rows[:limit]
        if path is not None:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "site",
                        "canonical_url",
                        "address",
                        "fingerprint",
                        "alias_count",
                        "total_spellings",
                        "alias_ratio",
                        "samples",
                    ],
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow({**row, "samples": " | ".join(row["samples"])})
        return rows

    # ------------------------------------------------------------- rejudging
    def rejudge(self, rules: NormalizeRules | None = None) -> dict:
        """Re-evaluate every historical address under the current rules."""
        if rules is not None:
            self.rules = rules

        old_entries = self._entries
        before = len(old_entries)

        # raw address -> the old entry that used to own it
        owner: dict[tuple[str, str], Entry] = {}
        for entry in old_entries.values():
            owner[(entry.site, entry.canonical_raw)] = entry
            for alias in entry.aliases.values():
                owner[(entry.site, alias.raw)] = entry

        grouped: dict[tuple[str, str], list[tuple[str, _Raw, Entry | None]]] = (
            defaultdict(list)
        )
        for (site, raw), info in self._raws.items():
            norm = self._normalize(raw)
            mode = self._mode_for(site)
            fp = legacy_fingerprint(raw) if mode == "legacy" else norm.fingerprint
            grouped[(site, fp)].append((raw, info, owner.get((site, raw))))

        rebuilt: dict[tuple[str, str], Entry] = {}
        merged_samples = []
        split_samples = []
        for (site, fp), rows in grouped.items():
            rows.sort(key=lambda row: (row[1].first_seen, row[0]))
            canonical_raw = rows[0][0]
            group_raws = {raw for raw, _, _ in rows}
            # An old entry only hands its fetch history to the group that
            # contains its own canonical address.  If new rules split an old
            # entry apart, the branch without its canonical starts fresh
            # instead of inheriting the old counters twice.
            contributors: list[Entry] = []
            for _, _, old in rows:
                if (
                    old is not None
                    and old.canonical_raw in group_raws
                    and old not in contributors
                ):
                    contributors.append(old)

            entry = Entry(
                site=site,
                fingerprint=fp,
                canonical_raw=canonical_raw,
                normalized_url=self._canonical_url(canonical_raw, site),
                first_seen=min(row[1].first_seen for row in rows),
            )
            for old in contributors:
                entry.fetches += old.fetches
                entry.observations += old.observations
                if old.last_fetch is not None:
                    entry.last_fetch = max(
                        old.last_fetch, entry.last_fetch or old.last_fetch
                    )
            if entry.observations:
                entry.change_rate = sum(
                    (old.change_rate or 0.0) * old.observations for old in contributors
                ) / entry.observations
            elif contributors:
                entry.change_rate = None

            for raw, info, _ in rows[1:]:
                old = owner.get((site, raw))
                alias = old.aliases.get(raw) if old is not None else None
                entry.aliases[raw] = Alias(
                    raw=raw,
                    first_seen=info.first_seen,
                    hits=alias.hits if alias is not None else 0,
                )
            rebuilt[(site, fp)] = entry

            if len(contributors) > 1:
                merged_samples.append(
                    {
                        "site": site,
                        "canonical_url": entry.normalized_url,
                        "merged": sorted({old.canonical_raw for old in contributors}),
                    }
                )
            detached = sorted(
                {
                    old.canonical_raw
                    for _, _, old in rows
                    if old is not None and old.canonical_raw not in group_raws
                }
            )
            if detached:
                split_samples.append(
                    {
                        "site": site,
                        "canonical_url": entry.normalized_url,
                        "detached_from": detached,
                    }
                )

        self._entries = rebuilt
        after = len(rebuilt)

        # Count real structural changes rather than a net difference.
        spread: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        for (site, raw), info in self._raws.items():
            norm = self._normalize(raw)
            fp = (
                legacy_fingerprint(raw)
                if self._mode_for(site) == "legacy"
                else norm.fingerprint
            )
            old = owner.get((site, raw))
            if old is not None:
                spread[(old.site, old.canonical_raw)].add((site, fp))
        split_count = sum(1 for fps in spread.values() if len(fps) > 1)

        return {
            "before_targets": before,
            "after_targets": after,
            "merged": len(merged_samples),
            "split": split_count,
            "merged_samples": merged_samples,
            "split_samples": split_samples,
        }

    # -------------------------------------------------------------- metrics
    def daily_metrics(self, site: str | None = None) -> dict:
        """Per-day alias ratio and duplicate fetch counts."""
        sites: Iterable[str] = [site] if site is not None else sorted(self._daily)
        out: dict[str, dict] = {}
        for name in sites:
            days = {}
            for day, counts in sorted(self._daily.get(name, {}).items()):
                seen = counts["links_seen"]
                aliases = counts["aliases"]
                days[day] = {
                    "links_seen": seen,
                    "new_targets": counts["new_targets"],
                    "aliases": aliases,
                    "alias_hits": counts["alias_hits"],
                    "alias_ratio": round(aliases / seen, 4) if seen else 0.0,
                    "due": counts["due"],
                    "fetches": counts["fetches"],
                    "duplicate_fetches": counts["duplicate_fetches"],
                    "legacy_due": counts["legacy_due"],
                    "avoided_duplicates": counts["avoided_duplicates"],
                    "observations": counts["observations"],
                    "content_changes": counts["content_changes"],
                }
            out[name] = days
        return out

    def explain(self, url) -> dict:
        """Show how *url* is judged without touching scheduling state."""
        raw = "" if url is None else str(url)
        norm = self._normalize(raw)
        site = norm.site
        mode = self._mode_for(site)
        fp = legacy_fingerprint(raw) if mode == "legacy" else norm.fingerprint
        entry = self._entries.get((site, fp))
        return {
            "url": raw,
            "fingerprint": fp,
            "canonical_url": norm.url,
            "site": site,
            "mode": mode,
            "known": entry is not None,
            "address": entry.canonical_raw if entry else None,
            "aliases": sorted(entry.aliases) if entry else [],
            "normalization": list(norm.rules),
        }

    def reset(self) -> None:
        """Drop all state (useful between isolated crawler runs)."""
        self._entries.clear()
        self._raws.clear()
        self._modes.clear()
        self._daily.clear()
        self._legacy_shadow.clear()


# --------------------------------------------------------------- default store
_default_store = LinkStore()


def fingerprint(url):
    return _default_store.fingerprint(url)


def due(url, now):
    return _default_store.due(url, now)


def observe(url, now, changed):
    return _default_store.observe(url, now, changed)


def alias_report(site=None):
    return _default_store.alias_report(site)


def alerts(site=None):
    return _default_store.alerts(site)


def site_alerts(site=None):
    return _default_store.site_alerts(site)


def export_aliases(path=None, site=None, limit=None):
    return _default_store.export_aliases(path, site, limit)


def rejudge(rules=None):
    return _default_store.rejudge(rules)


def daily_metrics(site=None):
    return _default_store.daily_metrics(site)


def explain(url):
    return _default_store.explain(url)


def start_trial(site):
    return _default_store.start_trial(site)


def promote(site):
    _default_store.promote(site)


def rollback(site):
    return _default_store.rollback(site)


def reset_state():
    _default_store.reset()


__all__ = [
    "BASE_INTERVAL",
    "Entry",
    "LinkStore",
    "NormalizeRules",
    "alias_report",
    "alerts",
    "daily_metrics",
    "due",
    "explain",
    "export_aliases",
    "fingerprint",
    "legacy_fingerprint",
    "normalize",
    "observe",
    "promote",
    "rejudge",
    "reset_state",
    "rollback",
    "site_alerts",
    "start_trial",
]
