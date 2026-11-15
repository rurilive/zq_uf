"""采集任务的重试编排：重试与熔断联动，预算按站点共享，恢复后阶梯放量。"""

import logging
import time

__all__ = ["outcome", "records", "report", "reset", "retry"]

_log = logging.getLogger("retryflow")

_MAX_ATTEMPT = 3            # 单任务重试上限（既有口径不变）
_SITE_BUDGET = 8            # 站点共享重试预算（令牌数，请求成功回补 1 个）
_MAX_AMPLIFICATION = 3.0    # 放大倍数上限：站点重试消耗 <= 3 x 站点不同任务数
_FAILURE_THRESHOLD = 3      # 连续失败达到即熔断
_RECOVER_AFTER = 30.0       # 熔断持续秒数，之后进入半开
_RAMP_STEPS = (1, 2, 4)     # 半开阶梯：每阶允许放行的重试令牌数
_RAMP_SUCCESS_PER_STEP = 2  # 每阶需要的连续成功数，走满阶梯才闭环

_attempts = {}
_sites = {}
_RECORDS = []


def _now():
    return time.monotonic()


class _SiteState:
    __slots__ = (
        "budget_used", "tasks", "consec_failures", "step_successes",
        "circuit", "opened_at", "ramp_step", "ramp_used",
    )

    def __init__(self):
        self.budget_used = 0
        self.tasks = set()
        self.consec_failures = 0
        self.step_successes = 0
        self.circuit = "closed"  # closed -> open -> half_open -> closed
        self.opened_at = None
        self.ramp_step = 0
        self.ramp_used = 0


def _check_name(value, label, where):
    if not isinstance(value, str):
        raise TypeError(
            "%s: %s 必须是非空字符串，收到类型 %s" % (where, label, type(value).__name__)
        )
    if not value:
        raise ValueError("%s: %s 必须是非空字符串，收到空串" % (where, label))


def _emit(action, st, task, site, attempts, reason=None, retry_after=None):
    """所有分支共用的返回结构，同时落记录和日志。"""
    rec = {
        "task": task,
        "site": site,
        "action": action,
        "attempts": attempts,
        "reason": reason,
        "circuit": st.circuit,
        "budget_remaining": _SITE_BUDGET - st.budget_used,
        "retry_after": retry_after,
    }
    _RECORDS.append(dict(rec))
    _log.info(
        "decision task=%r site=%r action=%s reason=%s attempts=%s circuit=%s "
        "budget_remaining=%d retry_after=%s",
        task, site, action, reason, attempts, st.circuit,
        rec["budget_remaining"], retry_after,
    )
    return rec


def retry(task, site):
    """失败任务的重试决策：熔断时等待不耗次数，预算按站点共享。"""
    _check_name(task, "task", "retryflow.retry")
    _check_name(site, "site", "retryflow.retry")
    st = _sites.setdefault(site, _SiteState())
    st.tasks.add(task)
    now = _now()

    if st.circuit == "open":
        wait = _RECOVER_AFTER - (now - st.opened_at)
        if wait > 0:
            return _emit("wait", st, task, site, _attempts.get(task, 0),
                         reason="circuit_open", retry_after=round(wait, 3))
        st.circuit = "half_open"
        st.ramp_step = 0
        st.ramp_used = 0
        st.step_successes = 0
        _emit("circuit_half_open", st, None, site, None, reason="recover_timer")

    if st.circuit == "half_open" and st.ramp_used >= _RAMP_STEPS[st.ramp_step]:
        return _emit("wait", st, task, site, _attempts.get(task, 0),
                     reason="ramp_limited")

    used = _attempts.get(task, 0)
    if used >= _MAX_ATTEMPT:
        return _emit("give_up", st, task, site, used, reason="max_attempts")
    if st.budget_used >= _SITE_BUDGET:
        return _emit("give_up", st, task, site, used, reason="site_budget_exhausted")
    if st.budget_used + 1 > _MAX_AMPLIFICATION * len(st.tasks):
        return _emit("give_up", st, task, site, used, reason="amplification_cap")

    _attempts[task] = used + 1
    st.budget_used += 1
    if st.circuit == "half_open":
        st.ramp_used += 1
    return _emit("retry", st, task, site, used + 1)


def report(task, site, success, error=None):
    """回报一次请求结果，驱动熔断、阶梯放量与预算回补。"""
    _check_name(task, "task", "retryflow.report")
    _check_name(site, "site", "retryflow.report")
    if not isinstance(success, bool):
        raise TypeError(
            "retryflow.report: success 必须是 bool，收到类型 %s" % type(success).__name__
        )
    if error is not None and not isinstance(error, str):
        raise TypeError(
            "retryflow.report: error 必须是 str 或 None，收到类型 %s" % type(error).__name__
        )
    st = _sites.setdefault(site, _SiteState())
    st.tasks.add(task)
    attempts = _attempts.get(task, 0)

    if success:
        st.consec_failures = 0
        if st.circuit == "closed":
            st.budget_used = max(0, st.budget_used - 1)  # 成功回补一个令牌
            return _emit("report", st, task, site, attempts, reason="success")
        st.step_successes += 1
        if st.step_successes >= _RAMP_SUCCESS_PER_STEP:
            st.step_successes = 0
            st.ramp_step += 1
            st.ramp_used = 0
            if st.ramp_step >= len(_RAMP_STEPS):
                st.circuit = "closed"
                st.budget_used = 0
                st.ramp_step = 0
                _emit("circuit_closed", st, None, site, None, reason="ramp_complete")
            else:
                _emit("ramp_step", st, None, site, None,
                      reason="step_%d" % st.ramp_step)
        return _emit("report", st, task, site, attempts, reason="success")

    st.consec_failures += 1
    st.step_successes = 0
    _log.warning(
        "report task=%r site=%r failure error=%r consec_failures=%d circuit=%s",
        task, site, error, st.consec_failures, st.circuit,
    )
    if st.circuit == "half_open":
        st.circuit = "open"
        st.opened_at = _now()
        st.ramp_step = 0
        st.ramp_used = 0
        _emit("circuit_open", st, None, site, None, reason="half_open_failure")
    elif st.circuit == "closed" and st.consec_failures >= _FAILURE_THRESHOLD:
        st.circuit = "open"
        st.opened_at = _now()
        _emit("circuit_open", st, None, site, None, reason="failure_threshold")
    return _emit("report", st, task, site, attempts,
                 reason="failure:%s" % error if error else "failure")


def outcome(task):
    """返回单个任务已经用掉的重试次数。"""
    _check_name(task, "task", "retryflow.outcome")
    return {"task": task, "attempts": _attempts.get(task, 0)}


def records():
    """返回全部决策与熔断联动记录的副本，可按 task/site 过滤定位。"""
    return [dict(rec) for rec in _RECORDS]


def reset():
    """清空全部任务计数、站点状态与记录。"""
    _attempts.clear()
    _sites.clear()
    _RECORDS.clear()
