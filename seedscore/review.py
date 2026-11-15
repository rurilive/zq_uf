"""Auditable restore log for seeds killed by the scorer.

A human can restore a seed that the filter dropped (a false kill).  Restores
are append-only events in a JSONL log; they can be exported, queried by actor,
target, site or time window, and replayed to rebuild the exact restore state
that an earlier filter run used.

Every event carries a stable ``id`` (content hash), the canonical ``target``,
the actor and a non-empty reason.  The log never mutates history: revoking a
restore appends a ``revoke`` event.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .config import resolve_config
from .urls import site_of, target_key, try_normalise

ACTIONS = {"restore", "revoke"}
_FIELDS = (
    "id",
    "action",
    "target",
    "url",
    "site",
    "actor",
    "reason",
    "at",
    "force",
    "score",
    "threshold",
    "note",
)


class ReviewError(ValueError):
    """Raised for an invalid review operation or log entry."""


def _now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _iso(value: datetime) -> str:
    return _now(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _now(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _now(datetime.fromisoformat(text))
    except ValueError:
        return None


def _canonical(target_or_url: str, config: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """Return ``(target_key, canonical_url)`` for a URL or target key."""
    raw = str(target_or_url or "").strip()
    if not raw:
        raise ReviewError("target is required")
    if "://" in raw or raw.startswith("//"):
        cfg = config or {}
        normalised, error = try_normalise(
            raw,
            tracking_params=cfg.get("tracking_params"),
            tracking_prefixes=cfg.get("tracking_prefixes"),
            session_params=cfg.get("session_params"),
        )
        if error or not normalised:
            raise ReviewError(f"invalid target url: {error}")
        return target_key(normalised), normalised
    return raw, ""


def _event_id(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _normalise_selection(items: Iterable[Any] | Any) -> list[Any]:
    if items is None:
        return []
    if isinstance(items, (str, bytes, Mapping)):
        return [items]
    try:
        return list(items)
    except TypeError:
        return [items]


class ReviewLog:
    """Append-only restore log, optionally backed by a JSONL file."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        config: Mapping[str, Any] | None = None,
        clock: Any = None,
    ) -> None:
        self.path = os.fspath(path) if path is not None else None
        self.config = resolve_config(config) if config else {}
        self._clock = clock
        self._entries: list[dict[str, Any]] = []
        if self.path and os.path.exists(self.path):
            self._entries.extend(_read_jsonl(self.path))
            self._reindex()

    # -- clock ----------------------------------------------------------
    def _time(self, now: datetime | str | None) -> datetime:
        if now is not None:
            parsed = _parse_time(now)
            if parsed is None:
                raise ReviewError(f"invalid timestamp: {now!r}")
            return parsed
        if callable(self._clock):
            return _now(self._clock())
        return _now()

    # -- internals ------------------------------------------------------
    def _reindex(self) -> None:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for entry in self._entries:
            entry_id = str(entry.get("id") or "")
            if entry_id and entry_id in seen:
                continue
            if entry_id:
                seen.add(entry_id)
            unique.append(entry)
        self._entries = unique

    def _append(self, entry: dict[str, Any]) -> dict[str, Any]:
        self._entries.append(entry)
        if self.path:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return entry

    # -- public API -----------------------------------------------------
    def restore(
        self,
        items: Iterable[Any] | Any,
        *,
        actor: str,
        reason: str,
        force: bool = False,
        score: float | None = None,
        threshold: float | None = None,
        at: datetime | None = None,
        note: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append restore events for one or more targets/URLs/seeds."""
        if not str(actor or "").strip():
            raise ReviewError("actor is required for a restore")
        if not str(reason or "").strip():
            raise ReviewError("reason is required for a restore")
        when = self._time(at)
        created: list[dict[str, Any]] = []
        for item in _normalise_selection(items):
            url = ""
            if isinstance(item, Mapping):
                url = str(item.get("url") or item.get("original_url") or "")
                raw = item.get("target") or url
                if item.get("score") is not None and score is None:
                    score_value = item.get("score")
                else:
                    score_value = score
            else:
                raw = str(item or "")
                score_value = score
            target, canonical = _canonical(raw, self.config)
            if url and not canonical:
                _, canonical = _canonical(url, self.config)
            record = {
                "action": "restore",
                "target": target,
                "url": canonical or url,
                "site": site_of(_host(canonical)) if canonical else "",
                "actor": str(actor).strip(),
                "reason": str(reason).strip(),
                "at": _iso(when),
                "force": bool(force),
                "score": _float(score_value),
                "threshold": _float(threshold),
                "note": note,
            }
            created.append(self._append(self._with_id(record)))
        return created

    def revoke(
        self,
        items: Iterable[Any] | Any,
        *,
        actor: str,
        reason: str,
        at: datetime | None = None,
        note: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append revoke events, cancelling earlier restores for the targets."""
        if not str(actor or "").strip():
            raise ReviewError("actor is required for a revoke")
        if not str(reason or "").strip():
            raise ReviewError("reason is required for a revoke")
        when = self._time(at)
        created: list[dict[str, Any]] = []
        for item in _normalise_selection(items):
            raw = item.get("target") or item.get("url") if isinstance(item, Mapping) else item
            target, canonical = _canonical(raw, self.config)
            record = {
                "action": "revoke",
                "target": target,
                "url": canonical,
                "site": site_of(_host(canonical)) if canonical else "",
                "actor": str(actor).strip(),
                "reason": str(reason).strip(),
                "at": _iso(when),
                "force": False,
                "score": None,
                "threshold": None,
                "note": note,
            }
            created.append(self._append(self._with_id(record)))
        return created

    def _with_id(self, record: Mapping[str, Any]) -> dict[str, Any]:
        entry = {field: record.get(field) for field in _FIELDS}
        base = {key: value for key, value in entry.items() if key != "id"}
        entry["id"] = _event_id(base)
        # Disambiguate identical events appended in the same second.
        counter = 1
        while any(item.get("id") == entry["id"] for item in self._entries):
            entry["id"] = _event_id({**base, "n": counter})
            counter += 1
        return entry

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    def restored_map(self, at: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Return target -> latest active restore record, up to ``at``."""
        cutoff = _parse_time(at) if at is not None else None
        state: dict[str, dict[str, Any]] = {}
        for entry in self._entries:
            when = _parse_time(entry.get("at"))
            if cutoff is not None and when is not None and when > cutoff:
                continue
            target = str(entry.get("target") or "")
            if not target:
                continue
            if entry.get("action") == "restore":
                state[target] = dict(entry)
            elif entry.get("action") == "revoke":
                state.pop(target, None)
        ordering = {entry.get("id"): index for index, entry in enumerate(self._entries)}
        return dict(
            sorted(
                state.items(),
                key=lambda item: ordering.get(item[1].get("id"), 0),
            )
        )

    def restored_targets(self, at: datetime | None = None) -> list[str]:
        return list(self.restored_map(at=at))

    def is_restored(self, item: Any, at: datetime | None = None) -> bool:
        target, _ = _canonical(item, self.config)
        return target in self.restored_map(at=at)

    def query(
        self,
        *,
        action: str | None = None,
        actor: str | None = None,
        target: str | None = None,
        site: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int | None = None,
        include_revoked: bool = True,
    ) -> list[dict[str, Any]]:
        """Filter log entries; time bounds are inclusive."""
        start = _parse_time(since)
        end = _parse_time(until)
        wanted_target = None
        if target is not None:
            wanted_target, _ = _canonical(target, self.config)
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            if action is not None and entry.get("action") != action:
                continue
            if actor is not None and str(entry.get("actor") or "") != str(actor):
                continue
            if wanted_target is not None and entry.get("target") != wanted_target:
                continue
            if site is not None:
                entry_site = entry.get("site") or site_of(_host(str(entry.get("url") or "")))
                if str(entry_site).lower() != str(site).lower():
                    continue
            when = _parse_time(entry.get("at"))
            if start is not None and when is not None and when < start:
                continue
            if end is not None and when is not None and when > end:
                continue
            results.append(dict(entry))
        if not include_revoked:
            # Keep only the restore that is currently active for each target;
            # a later re-restore after a revoke is active again.
            active_ids = {
                record.get("id") for record in self.restored_map().values()
            }
            results = [
                entry
                for entry in results
                if entry.get("action") == "restore" and entry.get("id") in active_ids
            ]
        if limit is not None:
            results = results[-max(0, int(limit)):]
        return results

    def export(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        format: str = "jsonl",
        entries: Sequence[Mapping[str, Any]] | None = None,
    ) -> str:
        """Export entries as JSONL or CSV; returns the serialised text."""
        rows = list(entries if entries is not None else self._entries)
        fmt = (format or "jsonl").lower()
        if fmt in {"jsonl", "json", "ndjson"}:
            text = "".join(
                json.dumps({field: row.get(field) for field in _FIELDS}, ensure_ascii=False, sort_keys=True)
                + "\n"
                for row in rows
            )
        elif fmt in {"csv", "tsv"}:
            delimiter = "\t" if fmt == "tsv" else ","
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=list(_FIELDS), delimiter=delimiter, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field) for field in _FIELDS})
            text = buffer.getvalue()
        else:
            raise ReviewError(f"unsupported export format: {format}")
        if path is not None:
            directory = os.path.dirname(os.path.abspath(os.fspath(path)))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(os.fspath(path), "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        return text

    def summary(self) -> dict[str, Any]:
        restores = [entry for entry in self._entries if entry.get("action") == "restore"]
        revokes = [entry for entry in self._entries if entry.get("action") == "revoke"]
        active = self.restored_map()
        actors: dict[str, int] = {}
        for entry in self._entries:
            actor = str(entry.get("actor") or "")
            actors[actor] = actors.get(actor, 0) + 1
        return {
            "entries": len(self._entries),
            "restores": len(restores),
            "revokes": len(revokes),
            "active": len(active),
            "actors": actors,
            "first": self._entries[0].get("at") if self._entries else None,
            "last": self._entries[-1].get("at") if self._entries else None,
            "path": self.path,
        }


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ReviewError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(payload, Mapping):
                raise ReviewError(f"{path}:{line_number}: entry must be an object")
            action = payload.get("action")
            if action not in ACTIONS:
                raise ReviewError(f"{path}:{line_number}: unknown action {action!r}")
            target = str(payload.get("target") or "")
            if not target:
                raise ReviewError(f"{path}:{line_number}: missing target")
            entry = {field: payload.get(field) for field in _FIELDS}
            if not entry.get("id"):
                entry["id"] = _event_id(
                    {key: value for key, value in entry.items() if key != "id"}
                )
            entries.append(entry)
    return entries


def replay(
    path: str | os.PathLike[str],
    *,
    config: Mapping[str, Any] | None = None,
    until: datetime | str | None = None,
) -> ReviewLog:
    """Rebuild a :class:`ReviewLog` from an exported log file.

    Replaying is deterministic: the same file always yields the same entries
    and the same active restore set.  ``until`` truncates the replay at a
    point in time, which is useful for reproducing an earlier admission run.
    """
    log = ReviewLog(config=config)
    for entry in _read_jsonl(os.fspath(path)):
        when = _parse_time(entry.get("at"))
        cutoff = _parse_time(until)
        if cutoff is not None and when is not None and when > cutoff:
            continue
        log._entries.append(entry)
    log._reindex()
    return log


def load(path: str | os.PathLike[str], *, config: Mapping[str, Any] | None = None) -> ReviewLog:
    """Alias for :func:`replay` when the whole log is wanted."""
    return ReviewLog(path, config=config)


__all__ = [
    "ACTIONS",
    "ReviewError",
    "ReviewLog",
    "load",
    "replay",
]
