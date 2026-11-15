"""Feedback-driven infinite-scroll collection.

The page, not a fixed round count, decides when scrolling stops:

* after every scroll the driver waits until ``loading`` is false and
  ``network_idle`` is true before reading items, so skeleton rows are never
  recorded;
* items are deduplicated by a stable identity (``key=`` / id / key / url);
* scrolling stops when the page reports the end (``at_end`` or
  ``has_more is False``), after ``idle_rounds`` consecutive scrolls that add
  nothing, when ``max_rounds`` is reached, or on timeout/error;
* a failure captures a snapshot of the page and the seen identities, so a
  later call can resume with ``seen=<result>`` without re-collecting.

Page protocol (a mapping or an object; missing fields use defaults)::

    loading       -> bool  (loading indicator / spinner / skeleton visible)
    network_idle  -> bool  (no in-flight XHR/fetch)
    has_more      -> bool | None
    at_end        -> bool  (end-of-list marker visible)
    items         -> sequence of currently rendered items
    collect       -> alternative callable for items
    scroll/scroll_once -> callable that performs one scroll
    error         -> exception raised by the page, if any
    snapshot      -> callable returning evidence for failure reports
    site / url    -> used for per-site stop-reason statistics

Callables are accepted for every field so live pages can be read lazily.
"""

from __future__ import annotations

import json
import math
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

__all__ = ["scroll", "done", "StopStats"]

_MISSING = object()
_KEY_FIELDS = ("id", "key", "url", "href")
_SKELETON_FIELDS = ("skeleton", "is_skeleton")
_MAX_SNAPSHOT_CHARS = 100_000

DEFAULT_MAX_ROUNDS = 30
DEFAULT_IDLE_ROUNDS = 2
DEFAULT_WAIT_TIMEOUT = 5.0
DEFAULT_POLL = 0.05


@dataclass(frozen=True)
class _Status:
    loading: bool
    network_idle: bool
    has_more: bool | None
    at_end: bool


class StopStats:
    """Stop reasons aggregated per site.

    >>> stats = StopStats()
    >>> stats.record("shop.local", "done")
    >>> stats.report()["shop.local"]["reasons"]["done"]
    1
    """

    def __init__(self) -> None:
        self._by_site: dict[str, Counter] = defaultdict(Counter)

    def record(self, site: str, reason: str) -> None:
        self._by_site[str(site)][str(reason)] += 1

    def counts(self, site: str | None = None) -> Counter:
        """Reason counts for one site, or for all sites combined."""
        if site is not None:
            return Counter(self._by_site.get(str(site), {}))
        total: Counter = Counter()
        for counter in self._by_site.values():
            total.update(counter)
        return total

    def report(self) -> dict[str, dict[str, Any]]:
        return {
            site: {"total": sum(counter.values()), "reasons": dict(sorted(counter.items()))}
            for site, counter in sorted(self._by_site.items())
        }

    def reset(self) -> None:
        self._by_site.clear()


def scroll(
    page: Any,
    collect: Callable[[], Iterable[Any]] | None = None,
    *,
    seen: Iterable[Any] | Mapping[str, Any] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    idle_rounds: int = DEFAULT_IDLE_ROUNDS,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    poll: float = DEFAULT_POLL,
    retries: int = 0,
    key: Callable[[Any], Any] | None = None,
    skeleton: Callable[[Any], bool] | None = None,
    site: str | None = None,
    stats: StopStats | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Scroll a page until it reports the end or stops producing items.

    Args:
        page: page handle implementing the feedback protocol above.
        collect: optional callable returning the items currently rendered. When
            omitted, ``page.items`` (or ``page.collect``) is used.
        seen: identities already collected by a previous call, so a failed run
            can resume without counting old items again. A previous result
            dict is accepted and its ``seen_items`` are used.
        max_rounds: hard cap on scroll attempts (default 30).
        idle_rounds: stop after this many consecutive scrolls with no new items
            (default 2).
        wait_timeout: seconds to wait for a stable page after each scroll.
        poll: seconds between feedback reads while waiting.
        retries: extra attempts for a scroll/collect that raised.
        key: item identity function. Defaults to id/key/url/href, then content.
        skeleton: predicate marking placeholder items; matching items are
            skipped and counted under ``skeletons``.
        site: site name for statistics. Defaults to ``page.site`` or the host
            of ``page.url``.
        stats: a :class:`StopStats` instance that receives the stop reason.
        clock, sleep: injectable time functions for tests.

    Returns:
        A dict with ``rounds`` (scrolls performed), ``added`` (new unique
        items), ``reason`` (``done``/``idle``/``max_rounds``/``timeout``/
        ``error``), ``seen`` (total unique identities), ``seen_items`` (keys
        for resuming), ``items``, ``skeletons``, ``site``, ``errors`` and
        ``events``.
    """
    max_rounds = _validate_int("max_rounds", max_rounds, minimum=0)
    idle_rounds = _validate_int("idle_rounds", idle_rounds, minimum=1)
    retries = _validate_int("retries", retries, minimum=0)
    wait_timeout = _validate_float("wait_timeout", wait_timeout, minimum=0)
    poll = _validate_float("poll", poll, minimum=0, strictly_positive=True)

    key_fn = key or _default_key
    store = _SeenStore(seen, key_fn)
    errors: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    added = 0
    skeletons = 0
    rounds = 0
    idle_streak = 0
    reason: str | None = None
    retries_left = retries
    resolved_site = _resolve_site(page, site)

    def collect_batch() -> tuple[bool, _Status, list[Any], int]:
        ok, status, raw = _collect_stable(
            page, collect, wait_timeout, poll, clock, sleep
        )
        batch, skipped = _usable_items(raw, skeleton)
        return ok, status, batch, skipped

    # Bootstrap: read what the initial viewport already shows without scrolling.
    try:
        ok, status, batch, skipped = collect_batch()
        skeletons += skipped
        if not ok:
            reason = "timeout"
            errors.append(_evidence(page, store, key_fn, 0, "timeout", _timeout_message(wait_timeout)))
            events.append({"round": 0, "outcome": "timeout", "added": 0})
        else:
            new = store.add(batch)
            added += new
            events.append(
                {
                    "round": 0,
                    "outcome": "bootstrap",
                    "added": new,
                    "skeletons": skipped,
                }
            )
            if _at_end(status):
                reason = "done"
    except Exception as exc:  # noqa: BLE001 - evidence is the point
        reason = "error"
        errors.append(_evidence(page, store, key_fn, 0, "exception", exc=exc))
        events.append({"round": 0, "outcome": "error", "error": _describe(exc)})

    while reason is None and rounds < max_rounds:
        rounds += 1
        try:
            _scroll_page(page)
            ok, status, batch, skipped = collect_batch()
            skeletons += skipped
            if not ok:
                reason = "timeout"
                errors.append(
                    _evidence(page, store, key_fn, rounds, "timeout", _timeout_message(wait_timeout))
                )
                events.append({"round": rounds, "outcome": "timeout", "added": 0})
                break

            new = store.add(batch)
            added += new
            idle_streak = 0 if new else idle_streak + 1
            outcome = "new" if new else "idle"
            events.append(
                {
                    "round": rounds,
                    "outcome": outcome,
                    "added": new,
                    "skeletons": skipped,
                    "idle_streak": idle_streak,
                    "has_more": status.has_more,
                    "at_end": status.at_end,
                }
            )
            if _at_end(status):
                reason = "done"
                break
            if idle_streak >= idle_rounds:
                reason = "idle"
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(_evidence(page, store, key_fn, rounds, "exception", exc=exc))
            events.append({"round": rounds, "outcome": "error", "error": _describe(exc)})
            if retries_left > 0 and rounds < max_rounds:
                retries_left -= 1
                continue
            reason = "error"
            break

    if reason is None:
        reason = "max_rounds"

    if stats is not None:
        stats.record(resolved_site, reason)

    return {
        "rounds": rounds,
        "added": added,
        "reason": reason,
        "done": reason == "done",
        "seen": len(store),
        "seen_items": list(store.keys()),
        "items": list(store.values()),
        "skeletons": skeletons,
        "site": resolved_site,
        "errors": errors,
        "events": events,
    }


def done(page: Any) -> dict[str, Any]:
    """Report whether the page is at the end, using page signals only.

    Height is deliberately ignored: virtualized lists keep a constant height
    while more rows exist. A page is done when it is stable and either shows an
    end marker or reports ``has_more is False``.
    """
    status = _read_status(page)
    if status.loading:
        return {"done": False, "reason": "loading"}
    if not status.network_idle:
        return {"done": False, "reason": "network_busy"}
    if status.at_end:
        return {"done": True, "reason": "end_marker"}
    if status.has_more is False:
        return {"done": True, "reason": "no_more"}
    if status.has_more is True:
        return {"done": False, "reason": "has_more"}
    return {"done": False, "reason": "no_signal"}


def _validate_int(name: str, value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}, got {value!r}")
    return value


def _validate_float(name: str, value: Any, *, minimum: float, strictly_positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if strictly_positive and value <= minimum:
        raise ValueError(f"{name} must be > {minimum}, got {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
    return float(value)


# ---------------------------------------------------------------------------
# page feedback
# ---------------------------------------------------------------------------


def _read_attr(obj: Any, name: str, default: Any = _MISSING) -> Any:
    """Read a page field, resolving a zero-argument callable if present."""
    if obj is None:
        return default
    value = obj.get(name, _MISSING) if isinstance(obj, Mapping) else getattr(obj, name, _MISSING)
    if value is _MISSING:
        return default
    return value() if callable(value) else value


def _read_status(page: Any) -> _Status:
    error = _read_attr(page, "error", None)
    if error:
        if isinstance(error, BaseException):
            raise error
        raise RuntimeError(str(error))

    has_more = _read_attr(page, "has_more", None)
    return _Status(
        loading=bool(_read_attr(page, "loading", False)),
        network_idle=bool(_read_attr(page, "network_idle", True)),
        has_more=None if has_more is None else bool(has_more),
        at_end=bool(_read_attr(page, "at_end", False)),
    )


def _read_batch(page: Any, collect: Callable[[], Iterable[Any]] | None) -> list[Any]:
    if collect is not None:
        source: Any = collect()
    else:
        source = _read_attr(page, "items", _MISSING)
        if source is _MISSING:
            source = _read_attr(page, "collect", None)
    if source is None:
        return []
    if isinstance(source, (str, bytes)):
        raise TypeError("items must be a sequence of items, not a string")
    return list(source)


def _collect_stable(
    page: Any,
    collect: Callable[[], Iterable[Any]] | None,
    wait_timeout: float,
    poll: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[bool, _Status, list[Any]]:
    """Wait for loading/network to settle, then read items and re-check.

    The second status read closes the race where reading items starts a new
    request: a batch read just as the page becomes busy is discarded rather
    than recorded as skeleton content.
    """
    deadline = clock() + wait_timeout
    max_polls = int(math.ceil(wait_timeout / poll))
    polls = 0

    while True:
        status = _read_status(page)
        if not status.loading and status.network_idle:
            batch = _read_batch(page, collect)
            after = _read_status(page)
            if not after.loading and after.network_idle:
                return True, after, batch
            status = after

        if polls >= max_polls:
            return False, status, []
        remaining = deadline - clock()
        if remaining <= 0:
            return False, status, []
        polls += 1
        sleep(min(poll, remaining))


def _scroll_page(page: Any) -> None:
    if page is None:
        return
    for name in ("scroll", "scroll_once"):
        if isinstance(page, Mapping):
            action = page.get(name, _MISSING)
        else:
            action = getattr(page, name, _MISSING)
        if action is not _MISSING and callable(action):
            action()
            return


def _at_end(status: _Status) -> bool:
    return status.at_end or status.has_more is False


# ---------------------------------------------------------------------------
# item identity and skeleton filtering
# ---------------------------------------------------------------------------


def _usable_items(batch: list[Any], skeleton: Callable[[Any], bool] | None) -> tuple[list[Any], int]:
    kept: list[Any] = []
    skipped = 0
    for item in batch:
        if _is_skeleton(item, skeleton):
            skipped += 1
        else:
            kept.append(item)
    return kept, skipped


def _is_skeleton(item: Any, predicate: Callable[[Any], bool] | None) -> bool:
    if predicate is not None:
        return bool(predicate(item))
    if isinstance(item, Mapping):
        return any(bool(item.get(name)) for name in _SKELETON_FIELDS)
    return any(bool(getattr(item, name, False)) for name in _SKELETON_FIELDS)


def _default_key(item: Any) -> Any:
    if isinstance(item, Mapping):
        for name in _KEY_FIELDS:
            value = item.get(name)
            if value is not None:
                return _hashable_key(value)
        return _content_key(item)
    for name in _KEY_FIELDS:
        value = getattr(item, name, None)
        if value is not None:
            return _hashable_key(value)
    attrs = getattr(item, "__dict__", None)
    if attrs:
        return _content_key(attrs)
    return _hashable_key(item)


def _hashable_key(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return _content_key(value)
    return value


def _content_key(value: Any) -> Any:
    try:
        return ("json", json.dumps(value, sort_keys=True, default=repr, ensure_ascii=False))
    except (TypeError, ValueError):
        return ("repr", repr(value))


class _SeenStore:
    def __init__(self, seen: Any, key_fn: Callable[[Any], Any]) -> None:
        self._items: dict[Any, Any] = {}
        self._key_fn = key_fn
        if seen is None:
            return
        if isinstance(seen, Mapping) and "seen_items" in seen:
            seen = seen["seen_items"]
        for value in seen:
            normalized = _hashable_key(value)
            self._items.setdefault(normalized, value)

    def add(self, batch: Iterable[Any]) -> int:
        new = 0
        for item in batch:
            identity = _hashable_key(self._key_fn(item))
            if identity not in self._items:
                self._items[identity] = item
                new += 1
        return new

    def keys(self) -> Iterable[Any]:
        return self._items.keys()

    def values(self) -> Iterable[Any]:
        return self._items.values()

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# failure evidence
# ---------------------------------------------------------------------------


def _evidence(
    page: Any,
    store: _SeenStore,
    key_fn: Callable[[Any], Any],
    round_no: int,
    kind: str,
    message: str | None = None,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "round": round_no,
        "kind": kind,
        "site": _resolve_site(page, None),
        "seen": len(store),
        "snapshot": _snapshot(page, store, key_fn),
    }
    if exc is not None:
        entry["error"] = _describe(exc)
        entry["traceback"] = traceback.format_exc()
    else:
        entry["error"] = message or kind
    return entry


def _snapshot(page: Any, store: _SeenStore, key_fn: Callable[[Any], Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        snapshot = _read_attr(page, "snapshot", _MISSING)
    except Exception as exc:  # noqa: BLE001
        snapshot = _MISSING
        data["snapshot_error"] = _describe(exc)

    if snapshot is not _MISSING and snapshot is not None:
        data["page"] = _truncate(snapshot)
    else:
        for name in ("url", "title", "site"):
            try:
                value = _read_attr(page, name, None)
            except Exception as exc:  # noqa: BLE001
                data[f"{name}_error"] = _describe(exc)
                continue
            if value is not None:
                data[name] = _truncate(value)

        try:
            status = _read_status(page)
            data.update(
                {
                    "loading": status.loading,
                    "network_idle": status.network_idle,
                    "has_more": status.has_more,
                    "at_end": status.at_end,
                }
            )
        except Exception as exc:  # noqa: BLE001
            data["status_error"] = _describe(exc)

        try:
            visible = _read_batch(page, None)
            data["visible_items"] = [
                _truncate(_hashable_key(key_fn(item))) for item in visible[:50]
            ]
        except Exception as exc:  # noqa: BLE001
            data["items_error"] = _describe(exc)

    data["seen_tail"] = [_truncate(key) for key in list(store.keys())[-20:]]
    return data


def _resolve_site(page: Any, site: str | None) -> str:
    if site:
        return str(site)
    try:
        for name in ("site", "url"):
            value = _read_attr(page, name, None)
            if not value:
                continue
            if name == "url":
                host = urlsplit(str(value)).netloc
                if host:
                    return host
                continue
            return str(value)
    except Exception:  # noqa: BLE001 - statistics must never break scrolling
        pass
    return "unknown"


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_SNAPSHOT_CHARS:
        return value[:_MAX_SNAPSHOT_CHARS] + f"...[{len(value) - _MAX_SNAPSHOT_CHARS} chars dropped]"
    return value


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _timeout_message(wait_timeout: float) -> str:
    return f"page did not become stable within {wait_timeout:g}s"
