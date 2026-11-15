"""按站点共享的失败预算、封禁冷却与人工复核清单。

一个 :class:`FailureGovernor` 实例管所有站点。同一站点下的所有任务共用
一份每日重试额度，因此站点级故障不会被 N 个任务放大成 N 份重试。

典型用法::

    gov = FailureGovernor(max_retries_per_site=10)
    decision = gov.record_failure("shop.example", exc, response, task_id="t1")
    if decision["action"] == "abandon":
        ...  # 已自动进入复核清单
    later = gov.requeue("t1")      # 复核后重新入队，不占当天额度

封禁冷却只记到 ``cooldown_until``，时间一到 :meth:`site_status` 和
:meth:`record_failure` 会自动视为已恢复，不需要改配置或手工清理。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .errors import BLOCKED, NOT_FOUND, REASONS, classify
from .policy import policy as reason_policy

_DEFAULT_MAX_RETRIES = 10


def _today(now):
    return datetime.fromtimestamp(now).date().isoformat()


def _iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


@dataclass
class _SiteState:
    """单个站点的当日状态。"""

    site: str = ""
    day: str = ""
    used: int = 0
    cooldown_until: float = 0.0
    failures: dict = field(default_factory=dict)

    def roll_day(self, day):
        """跨天时清空当日额度与失败构成，冷却按时间自然过期。"""
        if self.day != day:
            self.day = day
            self.used = 0
            self.failures = {reason: 0 for reason in REASONS}


class FailureGovernor:
    """站点级的失败账本。

    参数
    ----
    max_retries_per_site:
        每个站点每天可消耗的重试次数，所有任务共享。
    blocked_cooldown:
        封禁后站点暂停的秒数；到点自动恢复。
    clock:
        取当前时间的函数，默认 :func:`time.time`，测试时可注入。
    """

    def __init__(
        self,
        *,
        max_retries_per_site=_DEFAULT_MAX_RETRIES,
        blocked_cooldown=None,
        clock=None,
    ):
        if max_retries_per_site < 0:
            raise ValueError("max_retries_per_site 不能为负")
        if blocked_cooldown is None:
            # 与 :mod:`failclass.policy` 保持同一默认值。
            blocked_cooldown = reason_policy(BLOCKED)["cooldown"]
        if blocked_cooldown < 0:
            raise ValueError("blocked_cooldown 不能为负")
        self.max_retries_per_site = int(max_retries_per_site)
        self.blocked_cooldown = float(blocked_cooldown)
        self._clock = clock or time.time
        self._sites: dict[str, _SiteState] = {}
        self._review: list[dict] = []
        self._history: list[dict] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 状态

    def _now(self, now):
        return self._clock() if now is None else float(now)

    def _state(self, site, now, *, create=True):
        state = self._sites.get(site)
        if state is None:
            if not create:
                return None
            state = _SiteState(site=site)
            self._sites[site] = state
        state.roll_day(_today(now))
        if state.cooldown_until and now >= state.cooldown_until:
            # 冷却结束即自动恢复，这里只清理内存标记。
            state.cooldown_until = 0.0
        return state

    def _remaining(self, state):
        return max(0, self.max_retries_per_site - state.used)

    def site_status(self, site, *, now=None):
        """查看站点当前状态；冷却过期会在这里被自动解除。

        查询未知站点不会凭空创建账本，返回一份空状态。
        """
        now = self._now(now)
        with self._lock:
            state = self._state(site, now, create=False)
            if state is None:
                return {
                    "site": site,
                    "available": True,
                    "cooldown_until": None,
                    "cooldown_seconds": 0.0,
                    "retries_used": 0,
                    "retries_remaining": self.max_retries_per_site,
                    "in_cooldown": False,
                }
            return self._status_of(state, now)

    def _status_of(self, state, now):
        cooling = now < state.cooldown_until
        return {
            "site": state.site,
            "available": not cooling,
            "cooldown_until": _iso(state.cooldown_until),
            "cooldown_seconds": (
                round(state.cooldown_until - now, 3) if cooling else 0.0
            ),
            "retries_used": state.used,
            "retries_remaining": self._remaining(state),
            "in_cooldown": cooling,
        }

    # ------------------------------------------------------------------ 记账

    def record_failure(
        self,
        site,
        error,
        response=None,
        *,
        task_id=None,
        request_id=None,
        url=None,
        now=None,
    ):
        """记录一次失败并给出下一步动作。

        返回的字典包含 ``reason``、``action``、``remaining``（本站点当天
        还能重试几次）、``evidence``（状态码、响应头、请求标识、URL）等
        字段。``action`` 取值：``retry`` / ``switch_egress`` / ``wait`` /
        ``abandon``。放弃的任务会进入人工复核清单。
        """
        now = self._now(now)
        with self._lock:
            state = self._state(site, now)
            cooling = now < state.cooldown_until

            result = classify(
                error, response, request_id=request_id, url=url
            )
            reason = result["reason"]
            rule = reason_policy(reason)

            state.failures[reason] = state.failures.get(reason, 0) + 1

            retryable = reason != NOT_FOUND
            action = rule["action"]
            delay = rule["delay"]

            if reason == BLOCKED:
                # 封禁：换出口，并让整个站点暂停一段时间。
                state.cooldown_until = max(
                    state.cooldown_until, now + self.blocked_cooldown
                )
                action = "switch_egress"

            if not retryable:
                action = "abandon"
                delay = 0.0
            elif cooling and reason != BLOCKED:
                # 冷却期内不该再发请求；等站点恢复即可，不消耗额度。
                action = "wait"
                delay = max(0.0, state.cooldown_until - now)
            elif self._remaining(state) > 0:
                state.used += 1
            else:
                # 站点当日额度用尽，转人工复核，避免继续放大故障。
                action = "abandon"
                delay = 0.0

            remaining = self._remaining(state) if retryable else 0

            decision = {
                "site": site,
                "task_id": task_id,
                "request_id": request_id,
                "url": url,
                "reason": reason,
                "action": action,
                "remaining": remaining,
                "retryable": retryable,
                "delay": float(delay),
                "switch_egress": bool(rule["switch_egress"]),
                "budget_scope": "site",
                "retries_used": state.used,
                "budget_total": self.max_retries_per_site,
                "cooldown_until": (
                    _iso(state.cooldown_until)
                    if now < state.cooldown_until
                    else None
                ),
                "evidence": dict(result["evidence"], site=site, task_id=task_id),
                "at": _iso(now),
            }

            if action == "abandon":
                self._enqueue_review(
                    {
                        "site": site,
                        "task_id": task_id,
                        "reason": reason,
                        "url": url,
                        "request_id": request_id,
                        "evidence": copy.deepcopy(decision["evidence"]),
                        "abandoned_at": decision["at"],
                    }
                )

            self._history.append(copy.deepcopy(decision))
            return decision

    # ``on_failure`` 是更贴近回调场景的别名。
    on_failure = record_failure

    def record_success(self, site, *, task_id=None, request_id=None, now=None):
        """记录一次成功；解除本站点的封禁冷却。"""
        now = self._now(now)
        with self._lock:
            state = self._state(site, now)
            state.cooldown_until = 0.0
            entry = {
                "site": site,
                "task_id": task_id,
                "request_id": request_id,
                "action": "ok",
                "at": _iso(now),
            }
            self._history.append(entry)
            return dict(entry)

    # ------------------------------------------------------------------ 复核

    def _enqueue_review(self, entry):
        key = (entry["site"], entry.get("task_id"))
        if entry.get("task_id") is not None:
            for existing in self._review:
                if (existing["site"], existing.get("task_id")) == key:
                    existing.update(entry)
                    return
        self._review.append(entry)

    def review_tasks(self, site=None):
        """按站点（或全部）列出待人工复核的任务（深拷贝，可安全修改）。"""
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in self._review
                if site is None or item["site"] == site
            ]

    def requeue(self, task_id, site=None, *, now=None):
        """把复核过的任务重新入队，不消耗当天的重试额度。

        ``site`` 省略时按 ``task_id`` 在全部站点里查找。找不到时返回
        ``None``。
        """
        now = self._now(now)
        with self._lock:
            for index, item in enumerate(self._review):
                if item.get("task_id") != task_id:
                    continue
                if site is not None and item["site"] != site:
                    continue
                removed = self._review.pop(index)
                state = self._state(removed["site"], now)
                # 不增加 state.used：复核后重新入队不计入当天重试次数。
                decision = {
                    "site": removed["site"],
                    "task_id": task_id,
                    "request_id": removed.get("request_id"),
                    "action": "requeue",
                    "counted": False,
                    "budget_bypass": True,
                    "retries_used": state.used,
                    "budget_total": self.max_retries_per_site,
                    "reason": removed["reason"],
                    "evidence": copy.deepcopy(removed["evidence"]),
                    "at": _iso(now),
                }
                self._history.append(copy.deepcopy(decision))
                return decision
            return None

    def export_review(self, path, site=None):
        """把复核清单导出为 JSON 文件。

        ``site`` 为 ``None`` 时导出全部站点。返回 ``{"path", "site",
        "count"}``。
        """
        items = self.review_tasks(site)
        payload = {
            "site": site,
            "exported_at": _iso(self._now(None)),
            "count": len(items),
            "items": items,
        }
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return {"path": path, "site": site, "count": len(items)}

    # ------------------------------------------------------------------ 查询

    def failure_breakdown(self, site=None):
        """按站点统计当天失败构成（各原因次数）。"""
        now = self._now(None)
        with self._lock:
            if site is not None:
                state = self._state(site, now, create=False)
                if state is None:
                    return {reason: 0 for reason in REASONS}
                return {reason: state.failures.get(reason, 0) for reason in REASONS}
            return {
                name: {
                    reason: state.failures.get(reason, 0) for reason in REASONS
                }
                for name, state in sorted(self._sites.items())
            }

    def history(self, *, site=None, request_id=None):
        """按站点或请求标识追溯历史处理结果。"""
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in self._history
                if (site is None or item.get("site") == site)
                and (request_id is None or item.get("request_id") == request_id)
            ]
