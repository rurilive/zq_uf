"""任务时间预算：按类型、历史耗时和优先级分配，支持降级、断点恢复与审计。

预算口径：budget = clamp(预估耗时 * 优先级系数, MIN_BUDGET, MAX_BUDGET)。
预估耗时取该类型历史平均，无历史时用类型默认值。
降级口径：checkpoint 时按进度推算剩余所需，超过剩余预算则降级并记录原因；
剩余预算耗尽则提前失败，进度已保存，下次 start 从断点恢复。
"""

import json
import os
import time

TIMEOUT = 60  # 兼容旧引用：默认类型预估耗时（秒）
DEFAULT_ESTIMATE = float(TIMEOUT)
MIN_BUDGET = 1.0
MAX_BUDGET = 3600.0

TYPE_ESTIMATES = {"default": DEFAULT_ESTIMATE}
PRIORITY_MULTIPLIERS = {"high": 2.0, "normal": 1.0, "low": 0.5}
PRIORITIES = tuple(PRIORITY_MULTIPLIERS)


class BudgetError(Exception):
    """预算模块错误基类。"""


class InvalidJobError(BudgetError, ValueError):
    """任务描述非法（缺 name、未知 priority 等）。"""


class InvalidProgressError(BudgetError, ValueError):
    """进度数据非法（非 dict、负数、total<=0 等）。"""


def _normalize_job(job):
    if isinstance(job, str):
        if not job:
            raise InvalidJobError("job name must be a non-empty string")
        return {"name": job, "type": "default", "priority": "normal"}
    if isinstance(job, dict):
        name = job.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidJobError("job dict requires a non-empty 'name'")
        job_type = job.get("type", "default")
        priority = job.get("priority", "normal")
        if priority not in PRIORITY_MULTIPLIERS:
            raise InvalidJobError(
                "unknown priority %r, expected one of %s" % (priority, PRIORITIES)
            )
        return {"name": name, "type": job_type, "priority": priority}
    raise InvalidJobError("job must be a name string or a dict, got %r" % type(job).__name__)


def _validate_progress(progress):
    if not isinstance(progress, dict):
        raise InvalidProgressError("progress must be a dict, got %r" % type(progress).__name__)
    for key in ("done", "total"):
        if key in progress:
            value = progress[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise InvalidProgressError("progress[%r] must be a number" % key)
            if value < 0:
                raise InvalidProgressError("progress[%r] must be >= 0" % key)
    if "total" in progress and progress["total"] <= 0:
        raise InvalidProgressError("progress['total'] must be > 0")


class BudgetManager:
    """预算管理器。clock 可注入便于测试；checkpoint_dir/audit_file 可选持久化。"""

    def __init__(self, clock=None, history=None, checkpoint_dir=None, audit_file=None):
        self.clock = clock or time.monotonic
        self.history = {k: list(v) for k, v in (history or {}).items()}
        self.checkpoint_dir = checkpoint_dir
        self.audit_file = audit_file
        self._jobs = {}
        self._checkpoints = {}
        self.audit = []
        if checkpoint_dir:
            os.makedirs(checkpoint_dir, exist_ok=True)

    # ---- 预算 ----

    def estimate(self, job_type):
        samples = self.history.get(job_type)
        if samples:
            return sum(samples) / len(samples)
        return TYPE_ESTIMATES.get(job_type, DEFAULT_ESTIMATE)

    def budget_for(self, spec):
        raw = self.estimate(spec["type"]) * PRIORITY_MULTIPLIERS[spec["priority"]]
        return min(max(raw, MIN_BUDGET), MAX_BUDGET)

    # ---- 审计 ----

    def _audit(self, event, **fields):
        record = {"event": event, "ts": self.clock()}
        record.update(fields)
        self.audit.append(record)
        if self.audit_file:
            with open(self.audit_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---- 断点 ----

    def _checkpoint_path(self, name):
        safe = name.replace("/", "_").replace(os.sep, "_")
        return os.path.join(self.checkpoint_dir, safe + ".json")

    def _save_checkpoint(self, name, progress):
        self._checkpoints[name] = dict(progress)
        if self.checkpoint_dir:
            with open(self._checkpoint_path(name), "w", encoding="utf-8") as fh:
                json.dump(progress, fh, ensure_ascii=False)

    def _load_checkpoint(self, name):
        if name in self._checkpoints:
            return dict(self._checkpoints[name])
        if self.checkpoint_dir:
            path = self._checkpoint_path(name)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
        return None

    # ---- 接口 ----

    def start(self, job):
        """分配预算；若该任务有断点（上次超时/失败），从断点恢复并输出恢复结果。"""
        spec = _normalize_job(job)
        name = spec["name"]
        budget = self.budget_for(spec)
        prev = self._jobs.get(name)
        saved = self._load_checkpoint(name)
        resumed = bool(saved) and (prev is None or prev.get("status") in ("exhausted", "failed"))
        self._jobs[name] = {
            "spec": spec,
            "budget": budget,
            "started_at": self.clock(),
            "status": "running",
            "degraded": False,
            "reason": None,
        }
        self._audit(
            "budget_assigned", job=name, type=spec["type"], priority=spec["priority"],
            estimate=self.estimate(spec["type"]), budget=budget,
        )
        result = {
            "job": name,
            "budget": budget,
            "type": spec["type"],
            "priority": spec["priority"],
            "degraded": False,
            "resumed": resumed,
            "resume_from": saved if resumed else None,
        }
        if resumed:
            reason = "resumed after %s" % (prev["status"] if prev else "timeout")
            result["resume_reason"] = reason
            self._audit("resumed", job=name, from_progress=saved, reason=reason)
        return result

    def checkpoint(self, job, progress):
        """保存进度并检查预算。返回 status: ok / degraded / failed / completed。"""
        _validate_progress(progress)
        name = _normalize_job(job)["name"]
        rec = self._jobs.get(name)
        if rec is None or rec["status"] != "running":
            return {"job": name, "saved": False,
                    "error": "unknown or finished job: %s (call start first)" % name}
        self._save_checkpoint(name, progress)
        elapsed = self.clock() - rec["started_at"]
        remaining = rec["budget"] - elapsed
        done = progress.get("done")
        total = progress.get("total")

        if total and done is not None and done >= total:
            rec["status"] = "completed"
            self.history.setdefault(rec["spec"]["type"], []).append(elapsed)
            self._audit("completed", job=name, elapsed=elapsed)
            return {"job": name, "saved": True, "status": "completed",
                    "remaining": max(remaining, 0.0), "degraded": rec["degraded"],
                    "reason": rec["reason"], "progress": dict(progress)}

        if remaining <= 0:
            rec["status"] = "exhausted"
            rec["reason"] = "budget exhausted: %.1fs used of %.1fs" % (elapsed, rec["budget"])
            self._audit("failed", job=name, reason=rec["reason"], progress=dict(progress))
            return {"job": name, "saved": True, "status": "failed", "remaining": 0.0,
                    "degraded": rec["degraded"], "reason": rec["reason"],
                    "progress": dict(progress)}

        status = "ok"
        if done and total:
            needed = elapsed * (total - done) / done
            if needed > remaining:
                rec["degraded"] = True
                rec["reason"] = ("projected overrun: need %.1fs more, %.1fs remaining"
                                 % (needed, remaining))
                status = "degraded"
                self._audit("degraded", job=name, reason=rec["reason"],
                            progress=dict(progress))
        return {"job": name, "saved": True, "status": "degraded" if rec["degraded"] else status,
                "remaining": remaining, "degraded": rec["degraded"],
                "reason": rec["reason"], "progress": dict(progress)}


_default = BudgetManager()


def start(job):
    return _default.start(job)


def checkpoint(job, progress):
    return _default.checkpoint(job, progress)
