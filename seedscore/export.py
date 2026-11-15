"""Serialisation helpers for reports, score changes and restores.

Everything is exportable to JSONL or CSV so that an operator can diff two
runs, inspect why a seed was killed, or hand the restore history to an audit
tool.  CSV export flattens lists (``notes``, ``dead_reasons``) into ``;``
separated fields.
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Iterable, Mapping, Sequence

_COLUMNS = (
    "position",
    "status",
    "reason",
    "admission",
    "score",
    "threshold",
    "passes_threshold",
    "url",
    "target",
    "site",
    "source",
    "source_credibility",
    "history_yield",
    "link_quality",
    "dead",
    "dead_reasons",
    "blocked",
    "restored",
    "new_site",
    "notes",
)


class ExportError(ValueError):
    """Raised for an unsupported format."""


def _flat(record: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    parts = record.get("parts") if isinstance(record.get("parts"), Mapping) else {}
    contributions = (
        record.get("contributions")
        if isinstance(record.get("contributions"), Mapping)
        else {}
    )
    for column in _COLUMNS:
        value = record.get(column)
        if column in {"source_credibility", "history_yield", "link_quality"}:
            value = parts.get(column, contributions.get(column))
        if isinstance(value, (list, tuple)):
            value = ";".join(str(item) for item in value)
        elif isinstance(value, Mapping):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_isoformat)
        row[column] = value
    return row


def _isoformat(value: Any) -> str:
    """JSON fallback for datetimes and other non-serialisable seed values."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _serialise(
    records: Sequence[Mapping[str, Any]],
    fmt: str,
) -> str:
    lowered = (fmt or "jsonl").lower()
    if lowered in {"json", "jsonl", "ndjson"}:
        return "".join(
            json.dumps(dict(record), ensure_ascii=False, sort_keys=True, default=_isoformat)
            + "\n"
            for record in records
        )
    if lowered in {"csv", "tsv"}:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(_COLUMNS),
            delimiter="\t" if lowered == "tsv" else ",",
            extrasaction="ignore",
        )
        writer.writeheader()
        for record in records:
            writer.writerow(_flat(record))
        return buffer.getvalue()
    raise ExportError(f"unsupported export format: {fmt}")


def _write(path: str | os.PathLike[str] | None, text: str) -> str:
    if path is not None:
        directory = os.path.dirname(os.path.abspath(os.fspath(path)))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(os.fspath(path), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    return text


def report_records(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten a :func:`seedscore.filter` report into one row per seed.

    Seeds that were dead *and* already in the queue appear in ``dead``; they
    are emitted once, with ``removed_from_queue`` set.
    """
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for section, status in (("kept", "kept"), ("dropped", "dropped"), ("dead", "dead")):
        for record in report.get(section) or []:
            if id(record) in seen:
                continue
            seen.add(id(record))
            row = dict(record)
            row.setdefault("status", status)
            rows.append(row)
    return rows


def export_report(
    report: Mapping[str, Any],
    path: str | os.PathLike[str] | None = None,
    *,
    format: str = "jsonl",
    include_dead: bool = True,
) -> str:
    """Export kept/dropped/dead seeds of a filter report."""
    rows = report_records(report)
    if not include_dead:
        rows = [row for row in rows if row.get("status") != "dead"]
    return _write(path, _serialise(rows, format))


def export_changes(
    changes: Mapping[str, Any],
    path: str | os.PathLike[str] | None = None,
    *,
    format: str = "jsonl",
) -> str:
    """Export a ``changes`` diff (score deltas, adds, removals) as rows."""
    rows: list[dict[str, Any]] = []
    for change in changes.get("score_changes") or []:
        rows.append({"kind": "score_change", **change})
    for target in changes.get("added") or []:
        rows.append({"kind": "added", "target": target})
    for target in changes.get("removed") or []:
        rows.append({"kind": "removed", "target": target})
    for move in changes.get("moved") or []:
        rows.append({"kind": "moved", **move})
    for site, bucket in (changes.get("by_site") or {}).items():
        rows.append({"kind": "site_summary", **bucket})
    text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=_isoformat) + "\n"
        for row in rows
    )
    if (format or "jsonl").lower() in {"csv", "tsv"}:
        fields: list[str] = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=fields,
            delimiter="\t" if format.lower() == "tsv" else ",",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
    return _write(path, text)


def export_scores(
    records: Iterable[Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
    *,
    format: str = "jsonl",
) -> str:
    """Export raw score records (kept, dropped or a shadow pool)."""
    return _write(path, _serialise([dict(record) for record in records], format))


def export_restores(
    review: Any,
    path: str | os.PathLike[str] | None = None,
    *,
    format: str = "jsonl",
    **query: Any,
) -> str:
    """Export restore/revoke events from a :class:`seedscore.ReviewLog`."""
    entries = review.query(**query) if query else review.entries
    return review.export(path, format=format, entries=entries)


__all__ = [
    "ExportError",
    "export_changes",
    "export_report",
    "export_restores",
    "export_scores",
    "report_records",
]
