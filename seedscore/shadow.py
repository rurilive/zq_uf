"""Shadow-mode comparison against a human seed selection.

The new scorer is first run in shadow mode: it scores the candidate pool but
does not admit anything.  The shadow result is compared with the seeds a human
chose, so false kills -- especially of new sites -- are visible before the
scorer takes over.

All sampling is deterministic (a SHA-256 ordering seeded by ``seed``), so a
shadow run can be replayed exactly.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from .config import resolve_config
from .score import score
from .urls import normalise_order, target_key, try_normalise


def _target_of(item: Any, config: Mapping[str, Any] | None = None) -> str:
    if isinstance(item, Mapping):
        if item.get("target"):
            return str(item["target"])
        raw = str(item.get("url") or item.get("link") or "")
        if item.get("original_url"):
            raw = raw or str(item["original_url"])
    else:
        raw = str(item or "")
    if not raw:
        return ""
    normalised, error = try_normalise(
        raw,
        tracking_params=(config or {}).get("tracking_params"),
        tracking_prefixes=(config or {}).get("tracking_prefixes"),
        session_params=(config or {}).get("session_params"),
    )
    if error or not normalised:
        return raw
    return target_key(normalised)


def _score_records(
    seeds: Iterable[Mapping[str, Any] | str],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for index, raw in enumerate(seeds or []):
        seed = raw if isinstance(raw, Mapping) else {"url": str(raw)}
        result = score(seed, config)
        result["input_index"] = index
        if not result.get("target"):
            result["target"] = _target_of(seed, config)
        records.append(result)
    return records


def _stable_sample_key(seed: int, target: str, index: int) -> str:
    payload = f"{seed}:{target}:{index}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pick_dropped(
    records: Sequence[Mapping[str, Any]],
    shadow_targets: set[str],
    *,
    seed: int,
    sample_size: int | None,
    margin: float | None,
) -> tuple[list[Mapping[str, Any]], bool]:
    """Pick near-threshold and random dropped records deterministically."""
    dropped = [record for record in records if record.get("target") not in shadow_targets]
    if margin is not None:
        near = [
            record
            for record in dropped
            if record.get("threshold") is not None
            and (record.get("threshold") - float(record.get("score") or 0.0)) <= margin
        ]
        near.sort(
            key=lambda record: (
                -float(record.get("score") or 0.0),
                str(record.get("target")),
            )
        )
    else:
        near = []
    if sample_size is None:
        return dropped, True
    size = max(0, int(sample_size))
    chosen: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in near:
        target = str(record.get("target") or "")
        if len(chosen) >= size:
            break
        if target in seen:
            continue
        chosen.append(record)
        seen.add(target)
    remaining = [
        record
        for record in dropped
        if str(record.get("target") or "") not in seen
    ]
    remaining.sort(
        key=lambda record: _stable_sample_key(
            seed, str(record.get("target") or ""), int(record.get("input_index") or 0)
        )
    )
    for record in remaining:
        if len(chosen) >= size:
            break
        chosen.append(record)
        seen.add(str(record.get("target") or ""))
    return chosen, len(chosen) >= len(dropped)


def shadow(
    seeds: Iterable[Mapping[str, Any] | str],
    config: Mapping[str, Any] | None = None,
    *,
    human: Iterable[Any] | None = None,
    sample_size: int | None = None,
    margin: float | None = 0.1,
    seed: int = 0,
) -> dict[str, Any]:
    """Score seeds in shadow mode and compare with a human selection.

    Nothing is admitted.  ``human`` is the set of targets/URLs/seeds a human
    selected.  Every shadow-dropped live seed that the human kept counts as a
    false kill; false kills of seeds flagged ``new_site`` are reported
    separately because they are the main risk of a new scorer.  A human pick
    our data marks dead is reported under ``dead_reference`` instead, since
    that is a stale-status question rather than a scoring error.

    ``sample_size``/``margin`` control the deterministically chosen subset of
    dropped seeds offered for review.  ``verdict`` is one of:

    * ``no_reference`` -- no human selection supplied.
    * ``blocked`` -- the shadow scorer false-killed at least one human pick.
    * ``insufficient_coverage`` -- no false kills were found, but some alive
      new sites that the scorer dropped were not in the reviewed sample, so
      absence of evidence is not evidence that they are safe.
    * ``safe`` -- no false kills, and every at-risk new site was reviewed.
    """
    cfg = resolve_config(config)
    records = _score_records(seeds, cfg)
    by_target: dict[str, dict[str, Any]] = {}
    for record in records:
        target = str(record.get("target") or record.get("url") or "")
        if not target:
            continue
        current = by_target.get(target)
        if current is None or float(record.get("score") or 0.0) > float(
            current.get("score") or 0.0
        ):
            by_target[target] = record

    shadow_targets = {
        target
        for target, record in by_target.items()
        if record.get("passes_threshold") and not record.get("dead") and not record.get("blocked")
    }
    dead_targets = {
        target for target, record in by_target.items() if record.get("dead")
    }

    human_targets = normalise_order(human, config=cfg) if human else []
    human_set = set(human_targets)

    missed = [target for target in human_targets if target not in shadow_targets]
    extra = [target for target in shadow_targets if target not in human_set]
    overlap = [target for target in human_targets if target in shadow_targets]

    false_kills: list[dict[str, Any]] = []
    dead_reference: list[dict[str, Any]] = []
    for target in missed:
        record = by_target.get(target, {})
        item = {
            "target": target,
            "url": record.get("url"),
            "site": record.get("site"),
            "score": record.get("score"),
            "threshold": record.get("threshold"),
            "reason": record.get("reason"),
            "dead": bool(record.get("dead")),
            "new_site": bool(record.get("new_site")),
            "notes": list(record.get("notes") or []),
        }
        # A human pick our data calls dead is a stale-status signal, not a
        # scoring false kill; surface it separately.
        if item["dead"]:
            dead_reference.append(item)
        else:
            false_kills.append(item)
    new_site_false_kills = [item for item in false_kills if item.get("new_site")]

    sampled, complete = _pick_dropped(
        records,
        shadow_targets,
        seed=seed,
        sample_size=sample_size,
        margin=margin,
    )
    sampled_targets = {
        str(record.get("target") or record.get("url") or "") for record in sampled
    }
    sample_new_sites = sum(1 for record in sampled if record.get("new_site"))
    sample_human_hits = [
        target for target in sampled_targets if target in human_set
    ]

    # New sites that are alive but shadow-dropped are the ones at risk of a
    # false kill.  They must actually appear in the reviewed sample before the
    # scorer can be called safe.
    at_risk_new = [
        target
        for target, record in by_target.items()
        if record.get("new_site")
        and not record.get("dead")
        and target not in shadow_targets
    ]
    unreviewed_new = [target for target in at_risk_new if target not in sampled_targets]

    known_new_sites = sum(1 for record in by_target.values() if record.get("new_site"))
    total = len(by_target)
    recall = (len(overlap) / len(human_targets)) if human_targets else 0.0
    precision = (len(overlap) / len(shadow_targets)) if shadow_targets else 0.0

    if human is None:
        verdict = "no_reference"
        verdict_reason = "no human selection supplied"
    elif false_kills:
        verdict = "blocked"
        verdict_reason = f"{len(false_kills)} human pick(s) would be dropped"
    elif unreviewed_new:
        verdict = "insufficient_coverage"
        verdict_reason = (
            f"{len(unreviewed_new)} dropped new site(s) not covered by the reviewed "
            "sample; expand sample before enabling"
        )
    else:
        verdict = "safe"
        verdict_reason = "no false kills of human picks; all at-risk new sites reviewed"

    return {
        "mode": "shadow",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "sample": {
            "seed": seed,
            "size": len(sampled),
            "complete": complete,
            "sample_new_sites": sample_new_sites,
            "sample_human_hits": len(sample_human_hits),
            "margin": margin,
        },
        "counts": {
            "pool": total,
            "shadow_kept": len(shadow_targets),
            "human_kept": len(human_targets),
            "overlap": len(overlap),
            "missed": len(missed),
            "extra": len(extra),
            "false_kills": len(false_kills),
            "new_site_false_kills": len(new_site_false_kills),
            "dead": len(dead_targets),
            "dead_reference": len(dead_reference),
            "new_sites": known_new_sites,
            "unreviewed_new": len(unreviewed_new),
        },
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_kills": false_kills,
        "new_site_false_kills": new_site_false_kills,
        "dead_reference": dead_reference,
        "unreviewed_new": unreviewed_new,
        "missed": missed,
        "extra": extra,
        "human_targets": human_targets,
        "shadow_targets": sorted(shadow_targets),
        "sampled": [
            {
                "target": record.get("target") or record.get("url"),
                "url": record.get("url"),
                "site": record.get("site"),
                "score": record.get("score"),
                "threshold": record.get("threshold"),
                "reason": record.get("reason"),
                "new_site": bool(record.get("new_site")),
            }
            for record in sampled
        ],
        "config_version": cfg.get("version"),
    }


def compare_with_human(
    report: Mapping[str, Any],
    human: Iterable[Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-compare an existing shadow/filter report against another selection.

    This is useful when the human reference arrived after the shadow run.
    ``report`` must contain ``kept`` (filter output) or ``shadow_targets``.
    """
    cfg = resolve_config(config)
    if "shadow_targets" in report:
        shadow_targets = set(report.get("shadow_targets") or [])
    else:
        shadow_targets = {
            _target_of(record, cfg)
            for record in report.get("kept") or []
            if record
        }
    human_targets = normalise_order(human, config=cfg)
    human_set = set(human_targets)
    missed = [target for target in human_targets if target not in shadow_targets]
    extra = [target for target in shadow_targets if target not in human_set]
    overlap = [target for target in human_targets if target in shadow_targets]
    return {
        "counts": {
            "shadow_kept": len(shadow_targets),
            "human_kept": len(human_targets),
            "overlap": len(overlap),
            "missed": len(missed),
            "extra": len(extra),
        },
        "precision": round(len(overlap) / len(shadow_targets), 4) if shadow_targets else 0.0,
        "recall": round(len(overlap) / len(human_targets), 4) if human_targets else 0.0,
        "missed": missed,
        "extra": extra,
    }


def diff_reports(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    min_delta: float = 0.0,
) -> dict[str, Any]:
    """Diff two filter reports by target, with per-site aggregation.

    Detects seeds added to or removed from the queue, score changes (including
    those that crossed the threshold) and position moves.  The queue order of
    retained seeds is reported as ``order_changed`` so a caller can assert
    that a re-score did not reshuffle anything.
    """

    def collect(report: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        items: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for record in report.get("kept") or []:
            target = str(record.get("target") or record.get("url") or "")
            if not target:
                continue
            if target not in items:
                order.append(target)
            items[target] = record
        return items, order

    prev_items, prev_order = collect(previous or {})
    curr_items, curr_order = collect(current)
    added = [target for target in curr_order if target not in prev_items]
    removed = [target for target in prev_order if target not in curr_items]
    retained = [target for target in prev_order if target in curr_items]

    score_changes: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    prev_positions = {target: index for index, target in enumerate(prev_order)}
    curr_positions = {target: index for index, target in enumerate(curr_order)}
    for target in retained:
        before = prev_items[target]
        after = curr_items[target]
        old_score = float(before.get("score") or 0.0)
        new_score = float(after.get("score") or 0.0)
        delta = new_score - old_score
        if abs(delta) > min_delta or before.get("passes_threshold") != after.get("passes_threshold"):
            score_changes.append(
                {
                    "target": target,
                    "url": after.get("url") or before.get("url"),
                    "site": after.get("site") or before.get("site"),
                    "before": round(old_score, 6),
                    "after": round(new_score, 6),
                    "delta": round(delta, 6),
                    "crossed_threshold": before.get("passes_threshold")
                    != after.get("passes_threshold"),
                }
            )
        if prev_positions[target] != curr_positions[target]:
            moved.append(
                {
                    "target": target,
                    "before": prev_positions[target],
                    "after": curr_positions[target],
                }
            )

    by_site: dict[str, dict[str, Any]] = {}

    def bucket(site: Any) -> dict[str, Any]:
        key = str(site or "(unknown)")
        return by_site.setdefault(
            key,
            {
                "site": key,
                "added": 0,
                "removed": 0,
                "score_changed": 0,
                "max_abs_delta": 0.0,
            },
        )

    for target in added:
        bucket(curr_items[target].get("site"))["added"] += 1
    for target in removed:
        bucket(prev_items[target].get("site"))["removed"] += 1
    for change in score_changes:
        entry = bucket(change.get("site"))
        entry["score_changed"] += 1
        entry["max_abs_delta"] = round(
            max(entry["max_abs_delta"], abs(change["delta"])), 6
        )

    retained_sequence_prev = [target for target in prev_order if target in curr_items]
    retained_sequence_curr = [target for target in curr_order if target in prev_items]
    order_changed = retained_sequence_prev != retained_sequence_curr

    return {
        "added": added,
        "removed": removed,
        "score_changes": score_changes,
        "moved": moved,
        "order_changed": order_changed,
        "counts": {
            "previous": len(prev_items),
            "current": len(curr_items),
            "added": len(added),
            "removed": len(removed),
            "score_changed": len(score_changes),
            "moved": len(moved),
        },
        "by_site": by_site,
        "threshold": current.get("threshold"),
    }


__all__ = ["compare_with_human", "diff_reports", "shadow"]
