"""可配置的风控规则引擎。

规则以配置提供，条件、阈值和动作分开配置；命中时返回命中的规则和取值，
多条规则同时命中时按优先级取结论，回放与版本对比用于在调整前验证影响。
"""

from .config import (
    ConfigError,
    NotFoundError,
    activate,
    active_config,
    configure,
    history,
    load,
)
from .engine import compare, evaluate, explain, replay, reset, validate

__all__ = [
    "ConfigError",
    "NotFoundError",
    "activate",
    "active_config",
    "compare",
    "configure",
    "evaluate",
    "explain",
    "history",
    "load",
    "replay",
    "reset",
    "validate",
]
