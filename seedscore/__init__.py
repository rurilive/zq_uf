"""seedscore -- score seeds before they enter the crawl queue.

Main entry points::

    from seedscore import score, filter, shadow, diff_reports, ReviewLog

``score`` explains one seed; ``filter`` admits a batch (threshold, duplicate
collapse, dead-seed removal, stable queue order); ``shadow`` compares the
scorer with a human selection before it is enabled; ``ReviewLog`` records
restores of wrongly killed seeds and supports export/query/replay.
"""

from .config import DEFAULT_CONFIG, resolve_config
from .review import ReviewError, ReviewLog, load, replay
from .score import (
    COMPONENT_LABELS,
    COMPONENTS,
    dead_reasons,
    filter,
    is_new_site,
    score,
)
from .shadow import compare_with_human, diff_reports, shadow
from .urls import (
    URLError,
    normalise_url,
    site_of,
    target_key,
    try_normalise,
)

__all__ = [
    "COMPONENTS",
    "COMPONENT_LABELS",
    "DEFAULT_CONFIG",
    "ReviewError",
    "ReviewLog",
    "URLError",
    "compare_with_human",
    "dead_reasons",
    "diff_reports",
    "filter",
    "is_new_site",
    "load",
    "normalise_url",
    "replay",
    "resolve_config",
    "score",
    "shadow",
    "site_of",
    "target_key",
    "try_normalise",
]

__version__ = "2.0.0"
