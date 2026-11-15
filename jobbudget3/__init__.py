"""jobbudget3 —— 按任务分配时间预算，超时停止、降级留痕、断点续跑。

对外入口::

    from jobbudget3 import BudgetManager

    manager = BudgetManager(total_budget=8 * 3600, state_path=".jobbudget3/state.json")
    job = {"name": "backfill", "type": "backfill", "priority": 9, "history": [4300, 5100]}
    info = manager.start(job)          # budget / used / degraded_actions / resume_from
    ...
    manager.checkpoint("backfill", {"cursor": 500})
    ...
    manager.finish("backfill")

模块级函数 ``start`` / ``checkpoint`` / ``finish`` / ``resume`` / ``topup`` /
``status`` / ``actions`` 使用进程级默认管理器（受 ``JOBBUDGET_STATE`` /
``JOBBUDGET_TOTAL`` 环境变量控制）。
"""

from .budget import (
    ACTION_CONSERVE,
    ACTION_EARLY_FAIL,
    ACTION_MINIMAL,
    ACTION_REDUCE,
    ACTION_RESUME,
    ACTION_TIMEOUT,
    ACTION_TOPUP,
    DEFAULT_STATE_PATH,
    SHADOW_PREFIX,
    BudgetDenied,
    BudgetError,
    BudgetExhausted,
    BudgetManager,
    actions,
    checkpoint,
    default_manager,
    finish,
    resume,
    start,
    status,
    topup,
)
from .policy import (
    DEFAULT_BUDGET,
    DEFAULT_PRIORITY,
    LEGACY_BUDGET,
    TYPE_BUDGETS,
    allocate,
    history_estimate,
    normalize_priority,
    normalize_type,
    priority_multiplier,
)

__all__ = [
    "ACTION_CONSERVE",
    "ACTION_EARLY_FAIL",
    "ACTION_MINIMAL",
    "ACTION_REDUCE",
    "ACTION_RESUME",
    "ACTION_TIMEOUT",
    "ACTION_TOPUP",
    "BudgetDenied",
    "BudgetError",
    "BudgetExhausted",
    "BudgetManager",
    "DEFAULT_BUDGET",
    "DEFAULT_PRIORITY",
    "DEFAULT_STATE_PATH",
    "LEGACY_BUDGET",
    "SHADOW_PREFIX",
    "TYPE_BUDGETS",
    "actions",
    "allocate",
    "checkpoint",
    "default_manager",
    "finish",
    "history_estimate",
    "normalize_priority",
    "normalize_type",
    "priority_multiplier",
    "resume",
    "start",
    "status",
    "topup",
]
