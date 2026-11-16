"""兼容入口：规则已迁移到 riskrule.engine / riskrule.config。

保留 riskrule.rules 的旧导入路径，避免已有调用方失效。
"""

from .config import ConfigError, NotFoundError
from .engine import compare, evaluate, explain, replay, reset, validate

__all__ = [
    "ConfigError",
    "NotFoundError",
    "compare",
    "evaluate",
    "explain",
    "replay",
    "reset",
    "validate",
]
