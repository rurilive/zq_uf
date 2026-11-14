"""任务时间预算：按优先级与历史耗时分配，支持降级、断点恢复与审计。

兼容口径：start/checkpoint 收到未注册的字符串任务名时，保持旧行为
（统一 TIMEOUT、不保存进度）；传入 TaskSpec 或 dict 时进入调度路径。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

TIMEOUT = 60  # 未注册任务的兼容默认超时

DEGRADE_REDUCE = "reduce"  # 预算不足时用剩余预算降级执行
DEGRADE_FAIL = "fail"  # 预算不足时提前失败

CAUSE_DEGRADED = "degraded_budget_shortage"  # 降级后预算不足导致超时
CAUSE_ESTIMATE = "estimate_underestimated"  # 历史耗时低估导致超时
CAUSE_EXHAUSTED = "budget_exhausted"  # 总预算耗尽导致超时


@dataclass(frozen=True)
class TaskSpec:
    """可调度任务：priority 越大越优先。"""

    name: str
    task_type: str = "default"
    priority: int = 0


class History:
    """按任务类型累积历史耗时，估计取均值。"""

    def __init__(self):
        self._durations = {}

    def record(self, task_type, seconds):
        self._durations.setdefault(task_type, []).append(float(seconds))

    def estimate(self, task_type):
        values = self._durations.get(task_type)
        if not values:
            return None
        return sum(values) / len(values)


class AuditLog:
    """预算变化、降级、超时、恢复都写审计。"""

    def __init__(self, clock):
        self._clock = clock
        self.entries = []

    def record(self, event, task, detail):
        self.entries.append(
            {"time": self._clock(), "event": event, "task": task, "detail": detail}
        )


class _TaskState:
    def __init__(self, spec, estimate, started_at):
        self.spec = spec
        self.estimate = estimate
        self.started_at = started_at
        self.budget = 0.0
        self.degraded = False
        self.reason = ""
        self.failed = False
        self.progress = None
        self.timed_out = False
        self.cause = ""


class Scheduler:
    """按优先级和时间预算调度任务。

    policy 可以是 callable(priority) -> strategy，或 {priority: strategy}
    （"*" 为兜底），缺省全部 reduce。
    """

    def __init__(self, total_budget, history=None, policy=None, clock=None):
        self.total_budget = float(total_budget)
        self.history = history or History()
        self.policy = policy
        self.clock = clock or time.monotonic
        self.audit_log = AuditLog(self.clock)
        self._states = {}
        self._order = []

    def start(self, task):
        spec = self._normalize(task)
        if spec is None:
            return {"task": task, "budget": TIMEOUT}
        resumed = None
        old = self._states.get(spec.name)
        if old is not None and old.progress is not None:
            resumed = dict(old.progress)
            self.audit_log.record("resume", spec.name, {"from_progress": resumed})
        state = _TaskState(spec, self._estimate(spec), self.clock())
        if resumed is not None:
            state.progress = resumed
        self._states[spec.name] = state
        if spec.name not in self._order:
            self._order.append(spec.name)
        self._reallocate()
        result = {
            "task": spec.name,
            "budget": state.budget,
            "priority": spec.priority,
            "degraded": state.degraded,
        }
        if state.degraded or state.failed:
            result["reason"] = state.reason
        if state.failed:
            result["failed"] = True
        if resumed is not None:
            result["resumed_from"] = resumed
        return result

    def checkpoint(self, task, progress):
        name = task.name if isinstance(task, TaskSpec) else task
        if isinstance(name, dict):
            name = name.get("name")
        state = self._states.get(name)
        if state is None:
            return {"task": name, "saved": False}
        state.progress = dict(progress)
        elapsed = self.clock() - state.started_at
        if elapsed > state.budget and not state.timed_out:
            state.timed_out = True
            state.cause = self._attribute(state, elapsed)
            self.audit_log.record(
                "timeout",
                name,
                {"cause": state.cause, "elapsed": elapsed, "budget": state.budget},
            )
        return {
            "task": name,
            "saved": True,
            "elapsed": elapsed,
            "remaining_budget": max(0.0, state.budget - elapsed),
            "timed_out": state.timed_out,
        }

    def allocation(self):
        """预算分配输出。"""
        return {
            name: {
                "budget": self._states[name].budget,
                "estimate": self._states[name].estimate,
                "priority": self._states[name].spec.priority,
                "degraded": self._states[name].degraded,
            }
            for name in self._order
        }

    def degraded_tasks(self):
        """被降级或提前失败的任务及原因。"""
        return [
            {"task": name, "reason": st.reason, "budget": st.budget, "failed": st.failed}
            for name, st in self._states.items()
            if st.degraded or st.failed
        ]

    def resume_info(self, task):
        """断点恢复信息：上次保存的进度。"""
        state = self._states.get(task)
        if state is None or state.progress is None:
            return None
        return {"task": task, "progress": dict(state.progress)}

    def timeout_report(self):
        """超时归因输出。"""
        return [
            {
                "task": name,
                "cause": st.cause,
                "budget": st.budget,
                "estimate": st.estimate,
                "progress": dict(st.progress) if st.progress else None,
            }
            for name, st in self._states.items()
            if st.timed_out
        ]

    def audit(self):
        return list(self.audit_log.entries)

    def _normalize(self, task):
        if isinstance(task, TaskSpec):
            return task
        if isinstance(task, dict) and "name" in task:
            return TaskSpec(
                name=task["name"],
                task_type=task.get("task_type", "default"),
                priority=task.get("priority", 0),
            )
        return None

    def _estimate(self, spec):
        estimate = self.history.estimate(spec.task_type)
        return estimate if estimate is not None else float(TIMEOUT)

    def _strategy_for(self, priority):
        policy = self.policy
        if policy is None:
            return DEGRADE_REDUCE
        if callable(policy):
            return policy(priority)
        return policy.get(priority, policy.get("*", DEGRADE_REDUCE))

    def _reallocate(self):
        """按优先级从高到低分配，预算变化和降级写审计。"""
        remaining = self.total_budget
        ordered = sorted(
            self._order,
            key=lambda n: (-self._states[n].spec.priority, self._order.index(n)),
        )
        for name in ordered:
            st = self._states[name]
            old_budget, old_degraded, old_failed = st.budget, st.degraded, st.failed
            if remaining >= st.estimate:
                st.budget, st.degraded, st.reason, st.failed = (
                    st.estimate,
                    False,
                    "",
                    False,
                )
            elif self._strategy_for(st.spec.priority) == DEGRADE_FAIL or remaining <= 0:
                st.budget, st.degraded, st.reason, st.failed = (
                    0.0,
                    False,
                    CAUSE_EXHAUSTED,
                    True,
                )
            else:
                st.budget, st.degraded, st.reason, st.failed = (
                    remaining,
                    True,
                    "budget_shortage",
                    False,
                )
            remaining = max(0.0, remaining - st.budget)
            if st.budget != old_budget:
                self.audit_log.record(
                    "budget_change",
                    name,
                    {"from": old_budget, "to": st.budget},
                )
            if st.degraded and not old_degraded:
                self.audit_log.record(
                    "degraded", name, {"reason": st.reason, "budget": st.budget}
                )
            if st.failed and not old_failed:
                self.audit_log.record(
                    "fail_fast", name, {"reason": st.reason}
                )

    def _attribute(self, state, elapsed):
        if state.degraded:
            return CAUSE_DEGRADED
        if elapsed > state.estimate:
            return CAUSE_ESTIMATE
        return CAUSE_EXHAUSTED


_default_scheduler = None


def configure(total_budget=TIMEOUT * 3, history=None, policy=None):
    """重置模块级默认调度器。"""
    global _default_scheduler
    _default_scheduler = Scheduler(total_budget, history=history, policy=policy)
    return _default_scheduler


def _scheduler():
    global _default_scheduler
    if _default_scheduler is None:
        _default_scheduler = Scheduler(TIMEOUT * 3)
    return _default_scheduler


def start(task):
    """字符串任务名保持旧行为；TaskSpec/dict 进入预算调度。"""
    if isinstance(task, str):
        return {"task": task, "budget": TIMEOUT}
    return _scheduler().start(task)


def checkpoint(task, progress):
    """已注册任务保存进度；未注册任务保持旧行为。"""
    name = task.name if isinstance(task, TaskSpec) else task
    if isinstance(name, dict):
        name = name.get("name")
    if _default_scheduler is not None and name in _default_scheduler._states:
        return _default_scheduler.checkpoint(name, progress)
    return {"task": name, "saved": False}
