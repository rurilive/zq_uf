"""兼容旧导入路径：实现已拆到 engine / report / rules 等模块。"""

from .engine import FEEDBACK, analyze, compare, get_packet, load, query, reset
from .feedback import FeedbackStore, run_cases
from .report import report
from .rules import DEFAULT_VERSION, build_ruleset, register_ruleset

__all__ = [
    "analyze",
    "load",
    "report",
    "query",
    "compare",
    "get_packet",
    "reset",
    "run_cases",
    "FeedbackStore",
    "build_ruleset",
    "register_ruleset",
    "DEFAULT_VERSION",
    "FEEDBACK",
]
