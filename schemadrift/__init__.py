"""schemadrift：表结构漂移检测（基线比对 + 影响分级）。"""

from .alerting import build_alert, dispatch, should_alert
from .drift import LEVELS, KINDS, diff, filter_report
from .fingerprint import snapshot
from .store import (
    archive_report,
    baseline_path,
    check,
    list_baselines,
    load_baseline,
    save_baseline,
)

__all__ = [
    "KINDS",
    "LEVELS",
    "archive_report",
    "baseline_path",
    "build_alert",
    "check",
    "diff",
    "dispatch",
    "filter_report",
    "list_baselines",
    "load_baseline",
    "save_baseline",
    "should_alert",
    "snapshot",
]
