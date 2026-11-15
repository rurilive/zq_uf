"""按站点共享预算的失败路由器。

``FailureRouter`` 把 :func:`failclass.classify` 的归一化结果变成可执行动作：

* ``retry``          稍后重试（超时、普通失败）
* ``switch_egress``  换出口并进入冷却（封禁）
* ``give_up``        放弃并进入人工复核（页面不存在、预算耗尽）
* ``wait``           站点正在冷却，暂不发起请求

重试预算按站点计算并跨任务共享，按 UTC 自然日重置。
"复核后重新入队" 的任务会获得一次不计费的重试额度。
"""

from __future__ import annotations

import csv
import io
import json
import threading
from collections import Counter, OrderedDict, deque
from datetime import datetime, timedelta, timezone

from .errors import BLOCKED, NOT_FOUND, TIMEOUT, classify, policy, site_of

DEFAULT_BUDGET = 3
DEFAULT_BLOCKED_COOLDOWN = 300
MAX_COOLDOWN = 24 * 3600
UNKNOWN_SITE = "unknown"

REVIEW_PENDING = "pending"
REVIEW_REQUEUED = "requeued"


def _utc_now():
    return datetime.now(timezone.utc)


class FailureRouter:
    """站点级失败计数、冷却与人工复核清单。"""

    def __init__(self, budget=DEFAULT_BUDGET, blocked_cooldown=DEFAULT_BLOCKED_COOLDOWN,
                 clock=None, max_records=10000):
        if int(budget) < 0:
            raise ValueError("budget 不能为负数")
        self.budget = int(budget)
        self.blocked_cooldown = max(0, int(blocked_cooldown))
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._sites = {}
        self._task_site = {}
        self._review = OrderedDict()
        self._records = deque(maxlen=max_records)
        self._exempt = {}
        self._seq = 0

    # ------------------------------------------------------------------ 内部

    def _now(self):
        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return datetime.fromtimestamp(float(value), timezone.utc)

    @staticmethod
    def _day(now):
        return now.date().isoformat()

    def _state(self, site, now):
        state = self._sites.get(site)
        if state is None:
            state = {
                "site": site,
                "day": self._day(now),
                "used": 0,
                "counts": Counter(),
                "lifetime": Counter(),
                "cooldown_until": None,
                "attempts": {},
                "last_failure_at": None,
            }
            self._sites[site] = state
        elif state["day"] != self._day(now):
            # 新的一天，重试预算重新计算；冷却和历史统计保留。
            state["day"] = self._day(now)
            state["used"] = 0
            state["counts"] = Counter()
            state["attempts"] = {}
        return state

    def _resolve_site(self, task, url, evidence):
        if evidence.get("url"):
            site = site_of(evidence["url"])
            if site:
                return site
        if url:
            site = site_of(url)
            if site:
                return site
        return self._task_site.get(task, UNKNOWN_SITE)

    def _next_request_id(self, site):
        self._seq += 1
        return f"req-{self._seq}-{site}"

    def _record(self, now, **fields):
        self._seq += 1
        entry = {"seq": self._seq, "at": now.isoformat(), **fields}
        self._records.append(entry)
        return entry

    def _grant_review(self, task, site, reason, evidence, request_id, attempts, now):
        self._review[task] = {
            "task": task,
            "site": site,
            "reason": reason,
            "evidence": evidence,
            "request_id": request_id,
            "attempts": attempts,
            "status": REVIEW_PENDING,
            "created_at": now.isoformat(),
        }

    def _cooldown_for(self, evidence):
        retry_after = evidence.get("retry_after")
        if isinstance(retry_after, int) and retry_after > 0:
            return min(retry_after, MAX_COOLDOWN)
        return self.blocked_cooldown

    def _cooling_left(self, state, now):
        until = state["cooldown_until"]
        if not until or now >= until:
            return 0, None
        return int((until - now).total_seconds()), until

    def _result(self, reason, action, site, task, attempt, request_id, evidence,
                remaining, delay=0, cooldown=0, cooldown_until=None,
                review=False, cooling=False, charged=True):
        return {
            "reason": reason,
            "action": action,
            "delay": delay,
            "cooldown": cooldown,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "budget": "site",
            "remaining": remaining,
            "site": site,
            "task": task,
            "attempt": attempt,
            "request_id": request_id,
            "review": review,
            "cooling": cooling,
            "charged": charged,
            "evidence": evidence,
        }

    # ------------------------------------------------------------------ 主流程

    def gate(self, site, task=None):
        """发起请求前的检查：站点冷却中则返回 ``wait``，否则返回 ``None``。

        冷却到点后自动恢复，无需手工改配置。
        """

        target = site_of(site) or site
        with self._lock:
            now = self._now()
            state = self._sites.get(target)
            if state is None:
                return None
            left, until = self._cooling_left(state, now)
            if not left:
                return None
            return {
                "reason": BLOCKED,
                "action": "wait",
                "delay": left,
                "cooldown": left,
                "cooldown_until": until.isoformat(),
                "budget": "site",
                "remaining": max(0, self.budget - state["used"]),
                "site": target,
                "task": task,
                "attempt": None,
                "request_id": None,
                "review": False,
                "cooling": True,
                "charged": False,
                "evidence": None,
            }

    def handle(self, task, error, response=None, url=None, site=None, request_id=None):
        """处理一次失败，返回原因、下一步动作、剩余次数与证据。

        返回字典的关键字段：

        ``reason`` / ``action`` / ``evidence``
            归一化原因、下一步动作和证据（状态码、响应头、URL、原文）。
        ``remaining``
            本次之后该站点当天还剩的重试额度。为 0 时本次重试已是最后一次，
            再次失败会直接进入人工复核。
        ``delay`` / ``cooldown`` / ``cooldown_until``
            超时的退避秒数 / 封禁的冷却秒数与结束时间。
        ``request_id``
            可追溯到具体请求的标识（未提供时自动生成）。

        每次失败都会留下一条可追溯记录。站点冷却期内除 ``not_found`` 外的
        失败不计入统计、不消耗预算——那是调用方绕过了 :meth:`gate` 的结果。
        """

        classified = classify(error, response)
        reason = classified["reason"]
        evidence = dict(classified["evidence"])
        if url and not evidence.get("url"):
            evidence["url"] = str(url)
        if site:
            evidence["site"] = site
        with self._lock:
            now = self._now()
            site = (site and (site_of(site) or site)) or self._resolve_site(
                task, url, evidence
            )
            evidence["site"] = site
            self._task_site[task] = site
            state = self._state(site, now)
            request_id = request_id or self._next_request_id(site)
            attempt = state["attempts"].get(task, 0) + 1
            state["attempts"][task] = attempt
            remaining = max(0, self.budget - state["used"])
            left, until = self._cooling_left(state, now)

            # 冷却期内不应再发请求；出现的失败不计入统计、不扣预算，
            # 但延长冷却（封禁下的重试只会把出口一起封掉）。
            if left and reason != NOT_FOUND:
                if reason == BLOCKED:
                    extra = self._cooldown_for(evidence)
                    if extra > left:
                        until = now + timedelta(seconds=extra)
                        state["cooldown_until"] = until
                        left = extra
                self._record(now, task=task, site=site, request_id=request_id,
                             reason=reason, action="wait", attempt=attempt,
                             remaining=remaining, charged=False, evidence=evidence)
                return self._result(
                    reason, "wait", site, task, attempt, request_id, evidence,
                    remaining=remaining, cooldown=left, cooldown_until=until,
                    cooling=True, charged=False,
                )

            state["counts"][reason] += 1
            state["lifetime"][reason] += 1
            state["last_failure_at"] = now.isoformat()

            # 永久失败不消耗站点重试预算，直接进复核；其它失败每发生一次
            # 就从站点当天的重试预算里扣一次。复核后重新入队的任务有一次
            # 不计费的尝试额度。
            exempt = False
            had_credit = False
            if reason != NOT_FOUND:
                credits = self._exempt.get(task, 0)
                if credits > 0:
                    if credits == 1:
                        self._exempt.pop(task, None)
                    else:
                        self._exempt[task] = credits - 1
                    exempt = True
                    had_credit = True
                elif state["used"] < self.budget:
                    state["used"] += 1
                    had_credit = True
            charged = bool(had_credit and not exempt and reason != NOT_FOUND)
            remaining = max(0, self.budget - state["used"])

            action = "give_up"
            delay = 0
            cooldown = 0
            cooldown_until = None
            review = False

            if reason == NOT_FOUND:
                # 页面不存在：再打多少次都一样，直接放弃并记下来。
                action = "give_up"
                review = True
            elif reason == BLOCKED:
                # 封禁：换出口，并按 Retry-After（或默认冷却）停一段时间。
                cooldown = self._cooldown_for(evidence)
                cooldown_until = now + timedelta(seconds=cooldown)
                state["cooldown_until"] = cooldown_until
                if had_credit:
                    action = "switch_egress"
                else:
                    action = "give_up"
                    review = True
            elif had_credit:
                # 超时：等一会儿再试，退避时间随同一任务的尝试次数增长。
                action = "retry"
                delay = policy(reason)["delay"]
                if reason == TIMEOUT:
                    delay = min(60, delay * (2 ** max(0, attempt - 1)))
            else:
                action = "give_up"
                review = True

            if review:
                self._grant_review(task, site, reason, evidence, request_id,
                                   attempt, now)

            self._record(
                now, task=task, site=site, request_id=request_id, reason=reason,
                action=action, attempt=attempt, remaining=remaining,
                delay=delay, cooldown=cooldown,
                cooldown_until=cooldown_until.isoformat() if cooldown_until else None,
                charged=charged, exempt=exempt, evidence=evidence,
            )
            return self._result(
                reason, action, site, task, attempt, request_id, evidence,
                remaining=remaining, delay=delay, cooldown=cooldown,
                cooldown_until=cooldown_until, review=review, charged=charged,
            )

    # ------------------------------------------------------------------ 查询

    def is_cooling(self, site):
        site = site_of(site) or site
        with self._lock:
            state = self._sites.get(site)
            if not state:
                return False
            return bool(self._cooling_left(state, self._now())[0])

    def cooldown_remaining(self, site):
        site = site_of(site) or site
        with self._lock:
            state = self._sites.get(site)
            if not state:
                return 0
            return self._cooling_left(state, self._now())[0]

    def remaining(self, site):
        site = site_of(site) or site
        with self._lock:
            self._state(site, self._now())
            state = self._sites.get(site)
            return max(0, self.budget - state["used"])

    def summary(self, site=None):
        """按站点给出失败构成、剩余预算和冷却状态。"""

        with self._lock:
            now = self._now()
            sites = [site_of(site) or site] if site else list(self._sites)
            result = {}
            for name in sites:
                state = self._sites.get(name)
                if state is None:
                    result[name] = {
                        "site": name, "budget": self.budget, "used": 0,
                        "remaining": self.budget, "counts": {},
                        "total_failures": 0, "cooling": False,
                        "cooldown_remaining": 0, "review": 0,
                    }
                    continue
                # 跨日时先滚动预算，保证 summary 与 handle 看到的一致。
                state = self._state(name, now)
                left, _ = self._cooling_left(state, now)
                result[name] = {
                    "site": name,
                    "budget": self.budget,
                    "used": state["used"],
                    "remaining": max(0, self.budget - state["used"]),
                    "counts": dict(state["counts"]),
                    "lifetime": dict(state["lifetime"]),
                    "total_failures": sum(state["counts"].values()),
                    "cooling": bool(left),
                    "cooldown_remaining": left,
                    "review": sum(
                        1 for item in self._review.values() if item["site"] == name
                    ),
                }
            return result

    def history(self, site=None, reason=None, task=None, limit=None):
        """返回可追溯到具体请求的处理记录（新的在前）。"""

        with self._lock:
            items = reversed(self._records)
            out = []
            for entry in items:
                if site and entry.get("site") != (site_of(site) or site):
                    continue
                if reason and entry.get("reason") != reason:
                    continue
                if task and entry.get("task") != task:
                    continue
                out.append(dict(entry))
                if limit and len(out) >= limit:
                    break
            return out

    def review_list(self, site=None):
        with self._lock:
            items = [dict(item) for item in self._review.values()]
        if site:
            target = site_of(site) or site
            items = [item for item in items if item["site"] == target]
        return items

    # ------------------------------------------------------------------ 导出 / 复核

    def export_review(self, path=None, site=None, fmt="json"):
        """按站点导出人工复核清单，可直接写文件。"""

        items = self.review_list(site)
        fmt = (fmt or "json").lower()
        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                ["task", "site", "reason", "status", "attempts", "request_id",
                 "created_at", "status_code", "url", "message"]
            )
            for item in items:
                evidence = item.get("evidence") or {}
                writer.writerow([
                    item["task"], item["site"], item["reason"], item["status"],
                    item["attempts"], item.get("request_id") or "",
                    item["created_at"], evidence.get("status") or "",
                    evidence.get("url") or "", evidence.get("message") or "",
                ])
            text = buffer.getvalue()
        elif fmt == "json":
            text = json.dumps(items, ensure_ascii=False, indent=2)
        else:
            raise ValueError("fmt 只支持 json 或 csv")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        return text

    def requeue(self, task, site=None, note=None):
        """把复核过的任务放回队列，并给一次不计费的重试额度。"""

        with self._lock:
            item = self._review.get(task)
            if item is None:
                return None
            if site and item["site"] != (site_of(site) or site):
                return None
            entry = self._review.pop(task)
            entry["status"] = REVIEW_REQUEUED
            entry["requeued_at"] = self._now().isoformat()
            if note:
                entry["note"] = note
            self._exempt[task] = self._exempt.get(task, 0) + 1
            return entry
