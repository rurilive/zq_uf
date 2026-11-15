"""按任务分配时间预算，超时停止、降级留痕、断点续跑。

核心规则：

* 预算由 :mod:`jobbudget3.policy` 按任务类型、历史耗时和优先级算出；
* 资源池不够时整体降级（少给预算）或提前失败，并把原因写进账本；
* 执行过程中按剩余比例自动降级，超时立即停止，不再全速空转；
* 每次检查点都落盘，超时后可 ``resume`` 从断点继续；
* 高优任务可临时追加预算，所有降级/追加动作都带原因、可查询；
* ``shadow`` 模式只对比不拦截，用于新预算上线前的影子观察期。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from . import policy

STATE_VERSION = 2

DEFAULT_STATE_PATH = os.path.join(".jobbudget3", "state.json")

#: 降级/追加动作的固定名称，便于查询和统计。
ACTION_REDUCE = "reduce_budget"
ACTION_CONSERVE = "conserve"
ACTION_MINIMAL = "minimal"
ACTION_TIMEOUT = "timeout"
ACTION_TOPUP = "topup"
ACTION_RESUME = "resume"
ACTION_EARLY_FAIL = "early_fail"

#: 影子模式下记录的前缀。
SHADOW_PREFIX = "shadow:"

_DEFAULT = object()

_FINISHED_STATES = ("completed", "failed", "timed_out", "paused")
_RESUMABLE_STATES = ("timed_out", "failed", "paused")


class BudgetError(RuntimeError):
    """预算模块的错误基类。"""


class BudgetExhausted(BudgetError):
    """任务预算已耗尽；``result`` 里带着恢复位置和降级记录。"""

    def __init__(self, result: Mapping[str, Any]):
        self.result = dict(result)
        job = self.result.get("job", "?")
        used = self.result.get("used", 0)
        budget = self.result.get("budget", 0)
        super().__init__(f"任务 {job!r} 预算已耗尽（已用 {used}s / 预算 {budget}s）")


class BudgetDenied(BudgetError):
    """预算请求被策略拒绝（例如低优任务申请追加预算）。"""

    def __init__(self, reason: str, result: Mapping[str, Any] | None = None):
        self.reason = reason
        self.result = dict(result or {})
        super().__init__(reason)


def _now_iso(timestamp: float | None = None) -> str:
    moment = datetime.fromtimestamp(
        timestamp if timestamp is not None else time.time(), timezone.utc
    )
    return moment.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("状态文件内容不是 JSON 对象")
    return data


def _write_json_atomic(path: str, data: Mapping[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _new_state(total_budget: float | None) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "total_budget": total_budget,
        "jobs": {},
        "actions": [],
        "shadow": {"enabled": False, "since": None, "until": None, "records": []},
    }


def _job_name(job: Any) -> str:
    if isinstance(job, Mapping):
        name = job.get("name") or job.get("id") or job.get("job")
    else:
        name = job
    text = str(name).strip() if name is not None else ""
    return text or "unnamed"


def _job_spec(job: Any) -> dict[str, Any]:
    return dict(job) if isinstance(job, Mapping) else {"name": _job_name(job)}


def _as_seconds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number != number:  # NaN
        return None
    return number


class BudgetManager:
    """带持久化账本的单进程预算管理器。

    :param total_budget: 资源池总预算（秒）。``None`` 表示不限制。
    :param state_path: 账本文件。``None`` 表示只保存在内存里。
    :param shadow: 影子模式开关；开启后只观察、不拦截、不降级。
    :param shadow_days: 影子观察期天数；默认 7 天，到期自动退出影子模式。
    :param clock: 时间源，测试可注入假时钟。
    """

    def __init__(
        self,
        total_budget: Any = _DEFAULT,
        state_path: Any = _DEFAULT,
        shadow: bool = False,
        shadow_days: float | None = 7,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._lock = threading.RLock()

        if state_path is _DEFAULT:
            state_path = os.environ.get("JOBBUDGET_STATE", DEFAULT_STATE_PATH)
        self.state_path: str | None = state_path or None

        if total_budget is _DEFAULT:
            env_total = os.environ.get("JOBBUDGET_TOTAL")
            total_budget = _as_seconds(env_total) if env_total not in (None, "") else None
        else:
            total_budget = _as_seconds(total_budget)

        if self.state_path and os.path.exists(self.state_path):
            try:
                self._state = self._migrate(_read_json(self.state_path))
            except (OSError, ValueError):
                self._quarantine_corrupt_state()
                self._state = _new_state(total_budget)
        else:
            self._state = _new_state(total_budget)

        if total_budget is not None:
            self._state["total_budget"] = total_budget
        if shadow and not self.shadow_active():
            self.enable_shadow(days=shadow_days)

    # ------------------------------------------------------------------
    # 基础状态与持久化
    # ------------------------------------------------------------------
    def _quarantine_corrupt_state(self) -> None:
        assert self.state_path
        target = f"{self.state_path}.corrupt-{int(self._clock())}"
        try:
            os.replace(self.state_path, target)
        except OSError:
            pass

    def _migrate(self, state: dict[str, Any]) -> dict[str, Any]:
        base = _new_state(state.get("total_budget"))
        for key, value in state.items():
            if key not in base:
                continue
            if key == "jobs":
                if isinstance(value, dict):
                    base["jobs"] = {str(k): dict(v) for k, v in value.items() if isinstance(v, dict)}
            elif key == "actions":
                if isinstance(value, list):
                    base["actions"] = [dict(v) for v in value if isinstance(v, dict)]
            else:
                base[key] = value

        shadow = base.get("shadow")
        if not isinstance(shadow, dict):
            shadow = {}
        shadow.setdefault("enabled", False)
        shadow.setdefault("since", None)
        shadow.setdefault("until", None)
        if not isinstance(shadow.get("records"), list):
            shadow["records"] = []
        base["shadow"] = shadow

        for job_state in base["jobs"].values():
            job_state.setdefault("used_total", 0.0)
            job_state.setdefault("attempt_used", 0.0)
            job_state.setdefault("degraded_actions", [])
            job_state.setdefault("topups", [])
            job_state.setdefault("shadow_actions", [])
            job_state.setdefault("history", [])
            job_state.setdefault("requested_budget", None)
        base["version"] = STATE_VERSION
        return base

    def _save(self) -> None:
        if self.state_path:
            _write_json_atomic(self.state_path, self._state)

    @property
    def state(self) -> dict[str, Any]:
        """当前账本（只读用途；直接改它不会落盘）。"""
        return self._state

    @property
    def total_budget(self) -> float | None:
        return self._state.get("total_budget")

    # ------------------------------------------------------------------
    # 影子模式
    # ------------------------------------------------------------------
    def enable_shadow(self, days: float | None = 7) -> dict[str, Any]:
        """进入影子模式；``days`` 为观察期长度，``None`` 表示不设截止。"""
        with self._lock:
            now = self._clock()
            shadow = self._state["shadow"]
            shadow["enabled"] = True
            shadow["since"] = _now_iso(now)
            shadow["until"] = _now_iso(now + float(days) * 86400) if days is not None else None
            self._save()
            return dict(shadow)

    def disable_shadow(self) -> dict[str, Any]:
        with self._lock:
            self._state["shadow"]["enabled"] = False
            self._save()
            return dict(self._state["shadow"])

    def shadow_active(self) -> bool:
        shadow = self._state.get("shadow") or {}
        if not shadow.get("enabled"):
            return False
        deadline = _parse_iso(shadow.get("until"))
        if deadline is None:
            return True
        return self._clock() < deadline

    def shadow_report(self) -> dict[str, Any]:
        """影子期报告：预算与实际耗时对比，以及可能被误杀的任务。"""
        records = list(self._state["shadow"].get("records", []))
        overruns = [r for r in records if r.get("overrun", 0) > 0]
        would_timeout = [r for r in records if r.get("would_timeout")]
        # 只超出预算不到 50% 的任务：再宽一点就能跑完，属于“预算过紧导致的误杀”。
        false_kill = [
            r for r in would_timeout if r.get("overrun", 0) <= r.get("budget", 0) * 0.5
        ]
        ratios = [r["actual"] / r["budget"] for r in records if r.get("budget")]
        return {
            "shadow": True,
            "active": self.shadow_active(),
            "since": self._state["shadow"].get("since"),
            "until": self._state["shadow"].get("until"),
            "jobs": len(records),
            "overruns": len(overruns),
            "would_timeout": [r["job"] for r in would_timeout],
            "false_kill_risk": [r["job"] for r in false_kill],
            "max_overrun_ratio": round(max(ratios), 3) if ratios else None,
            "records": records,
        }

    # ------------------------------------------------------------------
    # 账本查询
    # ------------------------------------------------------------------
    def _record_action(self, job: str, action: str, reason: str, **extra: Any) -> dict[str, Any]:
        entry = {
            "at": _now_iso(self._clock()),
            "job": job,
            "action": action,
            "reason": reason,
        }
        entry.update(extra)
        self._state["actions"].append(entry)
        job_state = self._state["jobs"].get(job)
        if job_state is not None:
            job_state.setdefault("degraded_actions", []).append(entry)
        return entry

    def actions(self, job: Any = None) -> list[dict[str, Any]]:
        """查询降级/追加/恢复动作；``job`` 为空时返回全部。"""
        if job is None:
            return [dict(item) for item in self._state["actions"]]
        name = _job_name(job)
        return [dict(item) for item in self._state["actions"] if item.get("job") == name]

    def status(self, job: Any = None) -> dict[str, Any]:
        """查询任务的预算、已用时间、降级动作和恢复位置。"""
        with self._lock:
            if job is None:
                return {
                    "total_budget": self.total_budget,
                    "pool_available": self.pool_available(),
                    "jobs": {name: self._snapshot(name) for name in self._state["jobs"]},
                    "shadow": self.shadow_active(),
                }
            name = _job_name(job)
            if name not in self._state["jobs"]:
                raise KeyError(f"没有任务 {name!r} 的预算记录")
            return self._snapshot(name)

    def _elapsed(self, job_state: Mapping[str, Any]) -> float:
        """本次执行已用秒数（含正在运行的时间）。"""
        base = float(job_state.get("attempt_used", 0.0))
        if job_state.get("status") == "running" and job_state.get("attempt_started"):
            base += max(0.0, self._clock() - float(job_state["attempt_started"]))
        return base

    def _snapshot(self, name: str) -> dict[str, Any]:
        job_state = self._state["jobs"][name]
        used = self._elapsed(job_state)
        budget = float(job_state.get("budget", 0) or 0)
        remaining = max(0.0, budget - used)
        prior = float(job_state.get("used_total", 0.0))
        return {
            "job": name,
            "type": job_state.get("type"),
            "priority": job_state.get("priority"),
            "budget": budget,
            "used": round(used, 3),
            "used_total": round(prior + used, 3),
            "remaining": round(remaining, 3),
            "remaining_fraction": round(remaining / budget, 4) if budget else 0.0,
            "status": job_state.get("status"),
            "degraded": bool(job_state.get("degraded", False)),
            "degradation": job_state.get("degradation"),
            "reason": job_state.get("reason", ""),
            "degraded_actions": [dict(a) for a in job_state.get("degraded_actions", [])],
            "resume_from": job_state.get("progress"),
            "checkpoints": job_state.get("checkpoints", 0),
            "resumes": job_state.get("resumes", 0),
            "topups": [dict(t) for t in job_state.get("topups", [])],
            "deadline": job_state.get("deadline"),
        }

    # ------------------------------------------------------------------
    # 资源池
    # ------------------------------------------------------------------
    def _consumed(self) -> float:
        """所有任务已消耗（含运行中任务的整份预算预留）的秒数。"""
        consumed = 0.0
        for job_state in self._state["jobs"].values():
            prior = float(job_state.get("used_total", 0.0))
            attempt = float(job_state.get("attempt_used", 0.0))
            if job_state.get("status") == "running":
                # 运行中的任务按“已耗 + 剩余预算”整体预留，避免多个任务同时挤占。
                reserved = max(self._elapsed(job_state), float(job_state.get("budget", 0) or 0))
                consumed += prior + reserved
            else:
                consumed += prior + attempt
        return consumed

    def pool_available(self) -> float | None:
        """资源池剩余秒数；池不限量时返回 ``None``。"""
        total = self.total_budget
        if total is None:
            return None
        return max(0.0, float(total) - self._consumed())

    # ------------------------------------------------------------------
    # 启动 / 恢复
    # ------------------------------------------------------------------
    def start(
        self,
        job: Any = None,
        budget: float | None = None,
        *,
        resume: bool = False,
    ) -> dict[str, Any]:
        """为任务分配预算并开始计时。

        资源池不足时：剩余 >= 最低额度则缩减预算并记录 ``reduce_budget``；
        剩余低于最低额度则提前失败，返回 ``halted=True`` 和失败原因。
        影子模式下两者都只记录、不执行。
        """
        spec = _job_spec(job)
        name = _job_name(spec)
        with self._lock:
            existing = self._state["jobs"].get(name)

            if existing and existing.get("status") == "running" and not resume:
                result = self._snapshot(name)
                result["already_started"] = True
                return result
            if resume:
                if existing is None:
                    raise KeyError(f"没有任务 {name!r} 的预算记录，无法恢复")
                if existing.get("status") not in _RESUMABLE_STATES:
                    raise BudgetError(
                        f"任务 {name!r} 当前状态为 {existing.get('status')!r}，无法从断点继续"
                    )

            alloc_spec = dict(spec)
            if existing:
                alloc_spec.setdefault("type", existing.get("type"))
                alloc_spec.setdefault("priority", existing.get("priority"))
                alloc_spec.setdefault("history", existing.get("history"))
            allocation = policy.allocate(alloc_spec)

            requested = float(budget) if budget is not None else float(allocation["requested_budget"])
            if budget is None and resume and existing and existing.get("requested_budget"):
                # 恢复沿用上次的申请额度，保持同一次任务的预算可预期。
                requested = float(existing["requested_budget"])
            requested = max(float(policy.MIN_BUDGET), requested)

            available = self.pool_available()
            grant = requested
            degraded = False
            degradation: str | None = None
            reason = ""
            halted = False
            shadow = self.shadow_active()
            shadow_note = ""

            if available is not None:
                needed = policy.minimum_budget(requested, alloc_spec)
                if available < needed:
                    degradation = ACTION_EARLY_FAIL
                    reason = (
                        f"资源池剩余 {available:.0f}s，低于任务最低需求 {needed:.0f}s，"
                        "提前失败以避免无效执行"
                    )
                elif available < requested:
                    degradation = ACTION_REDUCE
                    reason = (
                        f"资源池仅剩 {available:.0f}s，预算由 {requested:.0f}s 降至 "
                        f"{int(available)}s；执行中将按剩余比例继续降级"
                    )

            if degradation and shadow:
                # 影子期：只记录本应发生的降级/失败，照常按原预算执行。
                shadow_note = SHADOW_PREFIX + degradation
                degradation = None
            elif degradation == ACTION_EARLY_FAIL:
                grant = 0.0
                halted = True
                degraded = True
            elif degradation == ACTION_REDUCE:
                grant = float(int(available or 0))
                degraded = True

            now = self._clock()
            if halted:
                job_state = existing if existing else self._blank_job_state(name, allocation)
                job_state.update(
                    status="failed",
                    budget=0.0,
                    requested_budget=requested,
                    degraded=True,
                    degradation=ACTION_EARLY_FAIL,
                    reason=reason,
                    attempt_started=None,
                    attempt_used=0.0,
                    deadline=None,
                )
                self._state["jobs"][name] = job_state
                entry = self._record_action(
                    name, ACTION_EARLY_FAIL, reason, available=int(available or 0)
                )
                self._save()
                result = self._snapshot(name)
                result.update(
                    requested_budget=requested,
                    halted=True,
                    remaining_pool=int(available or 0),
                    shadow=shadow,
                    action=entry,
                )
                return result

            if resume:
                assert existing is not None
                job_state = existing
                resume_from = job_state.get("progress")
                job_state["used_total"] = float(job_state.get("used_total", 0.0)) + self._elapsed(job_state)
                job_state["resumes"] = int(job_state.get("resumes", 0)) + 1
                job_state["attempt_started"] = now
                job_state["attempt_used"] = 0.0
                job_state["status"] = "running"
                job_state["degraded"] = False
                job_state["degradation"] = None
                job_state["reason"] = ""
                note = (
                    f"从断点继续（第 {job_state['resumes']} 次恢复），分配新预算 {grant:.0f}s"
                )
                entry = self._record_action(name, ACTION_RESUME, note, resume_from=resume_from)
            else:
                resume_from = None
                job_state = self._blank_job_state(name, allocation, history=(
                    existing.get("history", []) if existing else []
                ))
                job_state["attempt_started"] = now
                job_state["status"] = "running"
                self._state["jobs"][name] = job_state
                entry = None

            job_state["budget"] = grant
            job_state["requested_budget"] = requested
            job_state["degraded"] = degraded
            job_state["degradation"] = degradation
            job_state["reason"] = f"影子模式（未执行）：{reason}" if shadow_note else reason
            job_state["deadline"] = _now_iso(now + grant)

            if shadow_note:
                self._record_action(
                    name,
                    shadow_note,
                    f"影子模式：本应执行 {shadow_note[len(SHADOW_PREFIX):]}，实际未执行（{reason}）",
                    budget=int(grant),
                    requested=int(requested),
                )
            elif degraded and degradation:
                entry = self._record_action(
                    name,
                    degradation,
                    reason,
                    budget=int(grant),
                    requested=int(requested),
                )

            self._save()
            result = self._snapshot(name)
            result.update(
                requested_budget=requested,
                base_budget=allocation["base_budget"],
                history_estimate=allocation["history_estimate"],
                history_budget=allocation["history_budget"],
                priority_multiplier=allocation["priority_multiplier"],
                halted=False,
                resume_from=resume_from,
                remaining_pool=None if available is None else int(max(0.0, available - grant)),
                shadow=shadow,
                action=entry,
            )
            return result

    def _blank_job_state(
        self,
        name: str,
        allocation: Mapping[str, Any],
        history: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "type": allocation["type"],
            "priority": allocation["priority"],
            "budget": 0.0,
            "requested_budget": None,
            "status": "pending",
            "created_at": _now_iso(self._clock()),
            "updated_at": None,
            "finished_at": None,
            "attempt_started": None,
            "attempt_used": 0.0,
            "used_total": 0.0,
            "deadline": None,
            "progress": None,
            "checkpoints": 0,
            "resumes": 0,
            "degraded": False,
            "degradation": None,
            "reason": "",
            "degraded_actions": [],
            "topups": [],
            "shadow_actions": [],
            "history": list(history or []),
        }

    def resume(self, job: Any, budget: float | None = None) -> dict[str, Any]:
        """从已保存的断点继续执行。"""
        return self.start(job, budget=budget, resume=True)

    # ------------------------------------------------------------------
    # 检查点 / 超时 / 降级
    # ------------------------------------------------------------------
    def checkpoint(self, job: Any, progress: Any = None) -> dict[str, Any]:
        """保存进度并按剩余预算决定是否降级或停止。

        返回里始终带有 ``budget`` / ``used`` / ``degraded_actions`` /
        ``resume_from``；``halted=True`` 表示已超时，调用方应停止执行。
        """
        name = _job_name(job)
        with self._lock:
            job_state = self._state["jobs"].get(name)
            if job_state is None:
                started = self.start(_job_spec(job) if isinstance(job, Mapping) else {"name": name})
                if started.get("halted"):
                    return started
                job_state = self._state["jobs"][name]

            if job_state.get("status") in ("timed_out", "failed", "completed"):
                # 已停止的任务不再消耗预算；只回报停止状态和恢复位置。
                if progress is not None and job_state.get("status") != "completed":
                    job_state["progress"] = progress
                    self._save()
                result = self._snapshot(name)
                result.update(
                    halted=job_state.get("status") != "completed",
                    saved=progress is not None,
                    action=None,
                )
                result["shadow"] = self.shadow_active()
                return result

            if progress is not None:
                job_state["progress"] = progress
            job_state["checkpoints"] = int(job_state.get("checkpoints", 0)) + 1
            job_state["updated_at"] = _now_iso(self._clock())
            if job_state.get("status") != "running":
                job_state["status"] = "running"
                job_state["attempt_started"] = self._clock()
                job_state["attempt_used"] = 0.0

            used = self._elapsed(job_state)
            budget = float(job_state.get("budget", 0) or 0)
            remaining = max(0.0, budget - used)
            fraction = (remaining / budget) if budget else 0.0
            shadow = self.shadow_active()

            if shadow:
                result = self._shadow_checkpoint(name, job_state, used, budget)
            elif used >= budget:
                reason = (
                    f"已用 {used:.0f}s 达到预算 {budget:.0f}s，停止执行；"
                    "进度已保存，可用 resume 从断点继续"
                )
                job_state.update(
                    status="timed_out",
                    attempt_used=used,
                    attempt_started=None,
                    degraded=True,
                    degradation=ACTION_TIMEOUT,
                    reason=reason,
                    deadline=job_state.get("deadline"),
                )
                entry = self._record_action(name, ACTION_TIMEOUT, reason, used=int(used))
                result = self._snapshot(name)
                result.update(halted=True, saved=True, action=entry)
            else:
                level = self._degradation_level(fraction)
                entry = None
                if level:
                    order = {None: 0, ACTION_CONSERVE: 1, ACTION_MINIMAL: 2}
                    current = job_state.get("degradation")
                    if order.get(level, 0) > order.get(current, 0):
                        reason = self._degradation_reason(level, budget, remaining)
                        job_state.update(degraded=True, degradation=level, reason=reason)
                        entry = self._record_action(
                            name, level, reason, used=int(used), remaining=int(remaining)
                        )
                result = self._snapshot(name)
                result.update(halted=False, saved=True, action=entry)

            result["shadow"] = shadow
            result["resume_from"] = job_state.get("progress")
            self._save()
            return result

    def _degradation_level(self, fraction: float) -> str | None:
        if fraction <= policy.MINIMAL_FRACTION:
            return ACTION_MINIMAL
        if fraction <= policy.CONSERVE_FRACTION:
            return ACTION_CONSERVE
        return None

    def _degradation_reason(self, level: str, budget: float, remaining: float) -> str:
        if level == ACTION_MINIMAL:
            return (
                f"剩余 {remaining:.0f}s/{budget:.0f}s（{remaining / budget:.0%}），"
                "只保留必要步骤，跳过非关键校验与扩展分析"
            )
        return (
            f"剩余 {remaining:.0f}s/{budget:.0f}s（{remaining / budget:.0%}），"
            "降为保守模式：减少并行度、缩小单批规模"
        )

    def _shadow_checkpoint(
        self,
        name: str,
        job_state: dict[str, Any],
        used: float,
        budget: float,
    ) -> dict[str, Any]:
        """影子模式：只记录“本来会怎么处理”，不改变任务状态。"""
        would: list[str] = []
        if budget and used >= budget:
            would.append(ACTION_TIMEOUT)
        elif budget:
            level = self._degradation_level(max(0.0, budget - used) / budget)
            if level:
                would.append(level)
        recorded = job_state.setdefault("shadow_actions", [])
        for action in would:
            if action in recorded:
                continue
            recorded.append(action)
            self._record_action(
                name,
                SHADOW_PREFIX + action,
                f"影子模式：按新预算本应触发 {action}（已用 {used:.0f}s/预算 {budget:.0f}s），未实际执行",
                used=int(used),
                budget=int(budget),
            )
        result = self._snapshot(name)
        result.update(
            halted=False,
            saved=True,
            shadow=True,
            would=[SHADOW_PREFIX + action for action in would],
            action=None,
        )
        return result

    # ------------------------------------------------------------------
    # 追加预算
    # ------------------------------------------------------------------
    def topup(self, job: Any, seconds: float | None = None, reason: str = "") -> dict[str, Any]:
        """为高优任务临时追加预算。

        低于 :data:`policy.TOPUP_MIN_PRIORITY` 的请求会被拒绝（不抛异常，
        返回 ``granted=False`` 和拒绝原因），以便调用方按普通降级处理。
        """
        name = _job_name(job)
        with self._lock:
            job_state = self._state["jobs"].get(name)
            if job_state is None:
                raise KeyError(f"没有任务 {name!r} 的预算记录")
            priority = int(job_state.get("priority", policy.DEFAULT_PRIORITY))
            base_budget = float(job_state.get("budget", 0.0) or 0.0)
            amount = float(seconds) if seconds is not None else float(policy.default_topup(base_budget))
            if amount <= 0:
                raise ValueError("追加预算必须为正数")

            denial = ""
            status = job_state.get("status")
            if status not in ("running", "timed_out", "paused"):
                denial = f"任务状态为 {status!r}，只有运行中或刚超时的任务可以追加预算"
            if not denial and priority < policy.TOPUP_MIN_PRIORITY:
                denial = (
                    f"任务优先级 {priority} 低于 {policy.TOPUP_MIN_PRIORITY}，"
                    "只有高优任务可以临时追加预算"
                )
            available = self.pool_available()
            if not denial and available is not None and available < amount:
                denial = f"资源池剩余 {available:.0f}s，不足以追加 {amount:.0f}s"
            if denial:
                entry = self._record_action(
                    name, ACTION_TOPUP, f"追加预算被拒绝：{denial}", granted=False
                )
                self._save()
                result = self._snapshot(name)
                result.update(granted=False, requested=amount, action=entry, reason=denial)
                return result

            job_state["budget"] = base_budget + amount
            elapsed = self._elapsed(job_state)
            job_state["deadline"] = _now_iso(self._clock() + max(0.0, job_state["budget"] - elapsed))
            note = reason.strip() or "高优任务临近超时，临时追加预算"
            entry = self._record_action(name, ACTION_TOPUP, note, seconds=int(amount), granted=True)
            job_state.setdefault("topups", []).append(entry)
            self._save()
            result = self._snapshot(name)
            result.update(granted=True, requested=amount, action=entry, reason=note)
            result["topup"] = dict(entry)
            return result

    # ------------------------------------------------------------------
    # 检查 / 结束
    # ------------------------------------------------------------------
    def check(self, job: Any) -> dict[str, Any]:
        """超时则抛出 :class:`BudgetExhausted`，否则返回当前状态。"""
        result = self.checkpoint(job)
        if result.get("halted"):
            raise BudgetExhausted(result)
        return result

    def finish(
        self,
        job: Any,
        status: str = "completed",
        used: float | None = None,
    ) -> dict[str, Any]:
        """结束任务，记录实际耗时作为后续预算的历史依据。"""
        name = _job_name(job)
        with self._lock:
            job_state = self._state["jobs"].get(name)
            if job_state is None:
                raise KeyError(f"没有任务 {name!r} 的预算记录")
            # 重复调用 finish 只更新状态，不重复累计历史与影子记录。
            already_finished = job_state.get("finished_at") is not None and job_state.get("status") in _FINISHED_STATES
            elapsed = self._elapsed(job_state)
            actual = float(used) if used is not None else elapsed
            budget = float(job_state.get("budget", 0.0) or 0.0)
            job_state["status"] = status
            job_state["attempt_used"] = actual
            job_state["attempt_started"] = None
            job_state["finished_at"] = _now_iso(self._clock())

            if not already_finished:
                history = job_state.setdefault("history", [])
                history.append(round(actual, 3))
                del history[:-50]

            if self.shadow_active() and not already_finished:
                self._state["shadow"]["records"].append(
                    {
                        "job": name,
                        "budget": round(budget, 3),
                        "actual": round(actual, 3),
                        "overrun": round(actual - budget, 3),
                        "would_timeout": bool(budget) and actual > budget,
                        "would_degrade": bool(job_state.get("shadow_actions")),
                        "finished_at": job_state["finished_at"],
                    }
                )
            self._save()
            result = self._snapshot(name)
            result.update(actual=actual, shadow=self.shadow_active())
            return result


_default_manager: BudgetManager | None = None
_default_manager_key: tuple[Any, ...] | None = None


def default_manager() -> BudgetManager:
    """进程级默认管理器；环境变量变化时会重建。"""
    global _default_manager, _default_manager_key
    key = (os.environ.get("JOBBUDGET_STATE"), os.environ.get("JOBBUDGET_TOTAL"))
    if _default_manager is None or _default_manager_key != key:
        _default_manager = BudgetManager()
        _default_manager_key = key
    return _default_manager


def start(job: Any = None, budget: float | None = None, **kwargs: Any) -> dict[str, Any]:
    """便捷入口：用默认管理器启动任务。"""
    return default_manager().start(job, budget=budget, **kwargs)


def checkpoint(job: Any, progress: Any = None) -> dict[str, Any]:
    """便捷入口：保存进度并判断降级/超时。"""
    return default_manager().checkpoint(job, progress)


def finish(job: Any, status: str = "completed", used: float | None = None) -> dict[str, Any]:
    """便捷入口：结束任务。"""
    return default_manager().finish(job, status=status, used=used)


def resume(job: Any, budget: float | None = None) -> dict[str, Any]:
    """便捷入口：从断点恢复任务。"""
    return default_manager().resume(job, budget=budget)


def topup(job: Any, seconds: float | None = None, reason: str = "") -> dict[str, Any]:
    """便捷入口：追加预算。"""
    return default_manager().topup(job, seconds=seconds, reason=reason)


def status(job: Any = None) -> dict[str, Any]:
    """便捷入口：查询预算状态。"""
    return default_manager().status(job)


def actions(job: Any = None) -> list[dict[str, Any]]:
    """便捷入口：查询降级动作。"""
    return default_manager().actions(job)
