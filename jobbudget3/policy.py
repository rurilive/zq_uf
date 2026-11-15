"""Budget calculation policy.

The old scheduler gave every task the same one-hour budget.  This module
replaces that with a deterministic per-task allocation based on:

* task type -- different classes of work have different base budgets;
* history   -- the 90th percentile of previous durations plus a safety margin;
* priority  -- high-priority tasks get a larger multiplier.

The helpers are pure functions so an allocation can be reviewed or tested
without touching any stored state.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

#: Budget used by the legacy (fixed one hour) scheduler.
LEGACY_BUDGET = 3600

#: Fallback budget for an unspecified/unknown task type.
DEFAULT_BUDGET = 3600

#: Hard bounds for a single allocation.
MIN_BUDGET = 60
MAX_BUDGET = 24 * 3600

#: Priority conventions.
DEFAULT_PRIORITY = 5
MIN_PRIORITY = 1
MAX_PRIORITY = 10
PRIORITY_STEP = 0.15

#: History estimate is padded by this factor before being used as a budget.
HISTORY_FACTOR = 1.2
HISTORY_QUANTILE = 0.9

#: Only high-priority jobs may request a temporary top-up.
TOPUP_MIN_PRIORITY = 8
DEFAULT_TOPUP_FRACTION = 0.25
DEFAULT_TOPUP_MIN_SECONDS = 300

#: Remaining-budget thresholds at which execution switches to a cheaper mode.
CONSERVE_FRACTION = 0.25
MINIMAL_FRACTION = 0.10

#: A pool allocation below this share of the request is refused up front.
MIN_REQUIRED_FRACTION = 0.5

#: Base budgets by task type (seconds).  Small jobs no longer waste an hour
#: and large jobs are no longer killed at the hour mark.
TYPE_BUDGETS = {
    "quick": 600,
    "small": 1200,
    "report": 1800,
    "sync": 1800,
    "normal": 3600,
    "standard": 3600,
    "batch": 5400,
    "index": 5400,
    "etl": 7200,
    "backfill": 7200,
    "large": 9000,
}

_TYPE_ALIASES = {
    "default": "normal",
    "std": "normal",
    "medium": "normal",
    "mid": "normal",
    "long": "batch",
    "xl": "large",
    "xlarge": "large",
    "pipeline": "batch",
}


def normalize_type(value: Any) -> str:
    """Return a normalized task type name (unknown types are kept as-is)."""
    if value is None:
        return "normal"
    name = str(value).strip().lower()
    if not name:
        return "normal"
    return _TYPE_ALIASES.get(name, name)


def type_base_budget(name: str) -> int:
    """Base budget for a normalized task type."""
    return int(TYPE_BUDGETS.get(name, DEFAULT_BUDGET))


def normalize_priority(value: Any) -> int:
    """Coerce a priority to the supported 1..10 integer range."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return max(MIN_PRIORITY, min(MAX_PRIORITY, number))


def priority_multiplier(priority: Any) -> float:
    """Priority 5 is neutral; every step is worth 15% of the base budget."""
    return 1.0 + (normalize_priority(priority) - DEFAULT_PRIORITY) * PRIORITY_STEP


def _numbers(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            result.append(number)
    return result


def history_values(history: Any) -> list[float]:
    """Flatten the supported history shapes into positive durations.

    Accepted forms: a number, a sequence of numbers, or a mapping with a
    percentile/statistic key or a nested ``samples``/``durations`` list.
    """
    if history is None:
        return []
    if isinstance(history, bool):
        return []
    if isinstance(history, (int, float)):
        return _numbers([history])
    if isinstance(history, str):
        return _numbers([history])
    if isinstance(history, Mapping):
        for key in ("samples", "durations", "runs", "values", "history"):
            if key in history:
                nested = history_values(history[key])
                if nested:
                    return nested
        for key in ("p90", "p95", "p99", "max", "average", "avg", "mean", "median"):
            if key in history:
                return _numbers([history[key]])
        return []
    if isinstance(history, Sequence):
        return _numbers(history)
    return []


def history_estimate(history: Any) -> float | None:
    """Return the p90 duration from a history, or ``None`` when empty."""
    values = sorted(history_values(history))
    if not values:
        return None
    index = max(0, math.ceil(HISTORY_QUANTILE * len(values)) - 1)
    return values[min(index, len(values) - 1)]


def _clamp_budget(value: float) -> int:
    return int(max(MIN_BUDGET, min(MAX_BUDGET, round(value))))


def allocate(job: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compute the requested budget and the inputs that produced it."""
    job = job if isinstance(job, Mapping) else {}
    task_type = normalize_type(job.get("type", job.get("kind")))
    priority = normalize_priority(job.get("priority", DEFAULT_PRIORITY))
    base = type_base_budget(task_type)
    estimate = history_estimate(job.get("history", job.get("durations")))
    multiplier = priority_multiplier(priority)

    history_budget = estimate * HISTORY_FACTOR if estimate is not None else None
    reference = max(float(base), history_budget or 0.0)
    requested = _clamp_budget(reference * multiplier)

    return {
        "type": task_type,
        "priority": priority,
        "base_budget": base,
        "history_estimate": estimate,
        "history_budget": history_budget,
        "priority_multiplier": multiplier,
        "requested_budget": requested,
    }


def minimum_budget(requested: float, job: Mapping[str, Any] | None = None) -> float:
    """Smallest allocation that still makes sense for a job."""
    job = job if isinstance(job, Mapping) else {}
    override = job.get("min_budget")
    if override is not None:
        try:
            return max(float(MIN_BUDGET), float(override))
        except (TypeError, ValueError):
            pass
    return max(float(MIN_BUDGET), float(requested) * MIN_REQUIRED_FRACTION)


def default_topup(budget: float) -> int:
    """Suggested temporary top-up for a high-priority job."""
    return max(DEFAULT_TOPUP_MIN_SECONDS, int(round(float(budget) * DEFAULT_TOPUP_FRACTION)))
