"""采集链路的可传递重试预算：同一链路共享账本，耗尽立即失败。"""

MAX_ATTEMPTS = 3

_LEDGER_KEY = "_ledger"
_LAYER_KEY = "_layer"


class BudgetError(Exception):
    """预算使用方式不正确（任务未挂预算、预算参数非法等）。"""


class BudgetExhausted(BudgetError):
    """预算耗尽：立即失败，标记为不可重试，并携带放大倍数和放弃层级。"""

    def __init__(self, layer, amplification):
        self.layer = layer
        self.amplification = amplification
        self.retryable = False
        super().__init__(
            f"budget exhausted at layer {layer}: "
            f"amplification {amplification}x, not retryable"
        )


class _Ledger:
    """同一条链路共享的预算账本，父子、兄弟调用共同扣减。"""

    __slots__ = ("total", "remaining", "attempts")

    def __init__(self, total):
        self.total = total
        self.remaining = total
        self.attempts = 0


def with_budget(task, budget):
    """把预算挂到任务上。

    budget 为非负整数时建立新账本（根任务，层级 0）；
    budget 为已挂预算的父任务时共享其父级账本（子调用，层级 +1），
    剩余预算随账本传递，兄弟调用共用同一余额、不会透支。
    """
    if not isinstance(task, dict):
        raise BudgetError(f"task must be a dict, got {type(task).__name__}")
    task = dict(task)
    if isinstance(budget, bool):
        raise BudgetError("budget must be a non-negative int or a parent task, not bool")
    if isinstance(budget, int):
        if budget < 0:
            raise BudgetError(f"budget must be >= 0, got {budget}")
        ledger = _Ledger(budget)
        layer = 0
        task["budget"] = budget
    elif isinstance(budget, dict) and _LEDGER_KEY in budget:
        ledger = budget[_LEDGER_KEY]
        layer = budget[_LAYER_KEY] + 1
        task["budget"] = ledger.remaining
    else:
        raise BudgetError(
            "budget must be a non-negative int or a parent task wrapped by with_budget()"
        )
    task[_LEDGER_KEY] = ledger
    task[_LAYER_KEY] = layer
    return task


def attempt(task):
    """扣减一次预算并记录一次尝试；预算耗尽立即抛 BudgetExhausted。"""
    if not isinstance(task, dict) or _LEDGER_KEY not in task:
        raise BudgetError("task has no budget; wrap it with with_budget() first")
    ledger = task[_LEDGER_KEY]
    if ledger.remaining <= 0:
        raise BudgetExhausted(layer=task[_LAYER_KEY], amplification=ledger.attempts)
    ledger.remaining -= 1
    ledger.attempts += 1
    used = int(task.get("attempts", 0)) + 1
    task["attempts"] = used
    return {"attempt": used, "retry": used < MAX_ATTEMPTS and ledger.remaining > 0}
