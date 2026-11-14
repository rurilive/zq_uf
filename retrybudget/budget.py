"""采集链路的可传递重试预算。

同一链路共享一个预算池：父任务把剩余预算传给子调用，
每次尝试扣减一次，耗尽立即失败并标记不可重试。
"""

import threading

MAX_ATTEMPTS = 3

_STATE_KEY = "_budget_state"


class BudgetError(Exception):
    """预算未挂载或预算参数非法。"""


class _BudgetState:
    """链路共享的预算池，兄弟调用共同扣减，不能透支。"""

    def __init__(self, total):
        self.total = total
        self.remaining = total
        self.lock = threading.Lock()
        self.root_requests = 0
        self.granted = 0
        self.abandoned_at = None

    def consume(self, layer):
        """原子扣减一次预算，返回 (是否放行, 扣减后剩余)。"""
        with self.lock:
            if self.remaining <= 0:
                if self.abandoned_at is None:
                    self.abandoned_at = layer
                return False, 0
            self.remaining -= 1
            self.granted += 1
            return True, self.remaining

    def report(self):
        with self.lock:
            amplification = (
                self.granted / self.root_requests if self.root_requests else 0.0
            )
            return {
                "budget_total": self.total,
                "budget_remaining": self.remaining,
                "root_requests": self.root_requests,
                "attempts": self.granted,
                "amplification": round(amplification, 2),
                "abandoned_at": self.abandoned_at,
            }


def _layer_of(task):
    return task.get("layer", task.get("id", "<unknown>"))


def with_budget(task, budget):
    """把预算挂到任务上。

    budget 为正整数时开启新链路；为父任务 dict 时继承其共享预算池，
    此时子任务的 "budget" 字段记录挂载时刻的剩余预算。
    """
    if not isinstance(task, dict):
        raise BudgetError(f"task 必须是 dict，拿到 {type(task).__name__}")
    task = dict(task)
    if isinstance(budget, dict):
        state = budget.get(_STATE_KEY)
        if state is None:
            raise BudgetError("父任务没有预算状态，先用 with_budget 挂预算再传递")
    elif isinstance(budget, int) and not isinstance(budget, bool):
        if budget < 1:
            raise BudgetError(f"budget 必须为正整数，拿到 {budget}")
        state = _BudgetState(budget)
        state.root_requests += 1
    else:
        raise BudgetError(f"budget 必须是正整数或父任务 dict，拿到 {budget!r}")
    task["budget"] = budget if isinstance(budget, int) else state.remaining
    task[_STATE_KEY] = state
    return task


def attempt(task):
    """执行一次尝试；预算耗尽时立即失败并标记不可重试。"""
    if not isinstance(task, dict) or _STATE_KEY not in task:
        raise BudgetError("任务未挂预算，请先 with_budget(task, budget)")
    state = task[_STATE_KEY]
    granted, remaining = state.consume(_layer_of(task))
    if not granted:
        info = state.report()
        return {
            "attempt": int(task.get("attempts", 0)),
            "retry": False,
            "exhausted": True,
            "abandoned_at": info["abandoned_at"],
            "amplification": info["amplification"],
            "error": f"重试预算耗尽，在层级 {info['abandoned_at']} 放弃",
        }
    used = int(task.get("attempts", 0)) + 1
    task["attempts"] = used
    return {"attempt": used, "retry": used < MAX_ATTEMPTS and remaining > 0}


def budget_report(task):
    """输出链路预算统计：放大倍数与放弃层级。"""
    if not isinstance(task, dict) or _STATE_KEY not in task:
        raise BudgetError("任务未挂预算，无法统计")
    return task[_STATE_KEY].report()
