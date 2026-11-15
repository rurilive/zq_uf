"""Command line interface: ``python3 -m seedscore <command>``.

Input files are JSON arrays of seed objects, or JSONL with one seed per line.
``--config`` takes a JSON object merged over ``DEFAULT_CONFIG``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, Mapping

from .export import export_report, export_scores
from .review import ReviewError, ReviewLog, replay
from .score import filter as filter_seeds
from .score import score as score_seed
from .shadow import shadow as shadow_run


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_seeds(path: str) -> list[Any]:
    """Load a seed list from JSON or JSONL."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read().strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = [
                json.loads(line) for line in text.splitlines() if line.strip()
            ]
        if isinstance(payload, Mapping):
            return [payload]
        return list(payload)
    return [line for line in text.splitlines() if line.strip()]


def _dump(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


def _review(args: argparse.Namespace) -> ReviewLog:
    config = _read_json(args.config) if args.config else None
    return ReviewLog(args.log, config=config)


def cmd_score(args: argparse.Namespace) -> int:
    config = _read_json(args.config) if args.config else None
    seeds = load_seeds(args.seeds)
    _dump([score_seed(seed, config) for seed in seeds])
    return 0


def cmd_filter(args: argparse.Namespace) -> int:
    config = _read_json(args.config) if args.config else None
    seeds = load_seeds(args.seeds)
    queue = None
    if args.queue:
        queue = [
            line.strip()
            for line in open(args.queue, encoding="utf-8")
            if line.strip()
        ]
    review = None
    if args.log:
        review = ReviewLog(args.log, config=config)
    report = filter_seeds(
        seeds,
        config,
        limit=args.limit,
        queue=queue,
        review=review,
        restored=args.restore or None,
    )
    if args.export:
        export_report(report, args.export, format=args.export_format)
    if args.scores:
        export_scores(report["kept"] + report["dropped"], args.scores, format="jsonl")
    _dump(report)
    return 0


def cmd_shadow(args: argparse.Namespace) -> int:
    config = _read_json(args.config) if args.config else None
    seeds = load_seeds(args.seeds)
    human = load_seeds(args.human) if args.human else None
    _dump(
        shadow_run(
            seeds,
            config,
            human=human,
            sample_size=args.sample_size,
            margin=args.margin,
            seed=args.seed,
        )
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    log = _review(args)
    _dump(
        log.restore(
            args.target,
            actor=args.actor,
            reason=args.reason,
            force=args.force,
            at=args.at,
        )
    )
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    log = _review(args)
    entries = log.query(
        action=args.action,
        actor=args.actor,
        target=args.target,
        site=args.site,
        since=args.since,
        until=args.until,
        limit=args.limit,
        include_revoked=not args.active_only,
    )
    if args.export:
        log.export(args.export, format=args.export_format, entries=entries)
    _dump(entries)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    config = _read_json(args.config) if args.config else None
    log = replay(args.log, config=config, until=args.until)
    _dump({"entries": log.entries, "restored": log.restored_targets()})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedscore",
        description="Score seeds and admit them to a crawl queue.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    score_parser = sub.add_parser("score", help="score seeds and show the breakdown")
    score_parser.add_argument("seeds", help="JSON/JSONL seed file")
    score_parser.add_argument("--config", help="JSON config merged over defaults")
    score_parser.set_defaults(func=cmd_score)

    filter_parser = sub.add_parser("filter", help="admit seeds into a queue")
    filter_parser.add_argument("seeds", help="JSON/JSONL seed file")
    filter_parser.add_argument("--config")
    filter_parser.add_argument("--queue", help="file of current queue URLs, one per line")
    filter_parser.add_argument("--limit", type=int)
    filter_parser.add_argument("--log", help="review log JSONL to apply restores from")
    filter_parser.add_argument(
        "--restore",
        action="append",
        help="target or URL to restore (repeatable)",
    )
    filter_parser.add_argument("--export", help="write kept/dropped/dead rows here")
    filter_parser.add_argument("--export-format", default="csv", choices=["jsonl", "csv", "tsv"])
    filter_parser.add_argument("--scores", help="write per-seed scores here")
    filter_parser.set_defaults(func=cmd_filter)

    shadow_parser = sub.add_parser("shadow", help="shadow run against a human selection")
    shadow_parser.add_argument("seeds", help="JSON/JSONL candidate pool")
    shadow_parser.add_argument("--human", help="JSON/JSONL human-selected seeds")
    shadow_parser.add_argument("--config")
    shadow_parser.add_argument("--sample-size", type=int)
    shadow_parser.add_argument("--margin", type=float, default=0.1)
    shadow_parser.add_argument("--seed", type=int, default=0)
    shadow_parser.set_defaults(func=cmd_shadow)

    restore_parser = sub.add_parser("restore", help="record a manual restore")
    restore_parser.add_argument("target")
    restore_parser.add_argument("--log", required=True)
    restore_parser.add_argument("--config")
    restore_parser.add_argument("--actor", required=True)
    restore_parser.add_argument("--reason", required=True)
    restore_parser.add_argument("--force", action="store_true", help="also re-admit a dead seed")
    restore_parser.add_argument("--at", help="ISO timestamp (defaults to now)")
    restore_parser.set_defaults(func=cmd_restore)

    query_parser = sub.add_parser("query", help="query the review log")
    query_parser.add_argument("--log", required=True)
    query_parser.add_argument("--config")
    query_parser.add_argument("--action", choices=["restore", "revoke"])
    query_parser.add_argument("--actor")
    query_parser.add_argument("--target")
    query_parser.add_argument("--site")
    query_parser.add_argument("--since")
    query_parser.add_argument("--until")
    query_parser.add_argument("--limit", type=int)
    query_parser.add_argument("--active-only", action="store_true")
    query_parser.add_argument("--export")
    query_parser.add_argument("--export-format", default="jsonl", choices=["jsonl", "csv", "tsv"])
    query_parser.set_defaults(func=cmd_query)

    replay_parser = sub.add_parser("replay", help="replay the review log")
    replay_parser.add_argument("--log", required=True)
    replay_parser.add_argument("--config")
    replay_parser.add_argument("--until")
    replay_parser.set_defaults(func=cmd_replay)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args) or 0)
    except (ReviewError, OSError, ValueError) as exc:
        sys.stderr.write(f"seedscore: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
