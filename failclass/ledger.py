"""站点级失败账本：预算共享、封禁冷却、人工复核清单。

同一站点的失败一起计算（不按任务各算一份）；封禁冷却结束自动恢复；
放弃的任务进人工复核清单，复核后重新入队不计入当天重试次数。
"""

import json
import time
from collections import Counter, defaultdict

from .errors import REASON_BANNED, classify, policy


class SiteLedger:
    """按站点记录失败、执行处置策略、维护复核清单。"""

    def __init__(self, site_budget=10, now=time.time):
        # site_budget: 每站点每天共享的重试次数上限。
        self.site_budget = site_budget
        self._now = now
        self._seq = 0
        self._attempts = defaultdict(int)      # site -> 当天已用重试次数
        self._records = defaultdict(list)      # site -> 失败记录（可追溯到具体请求）
        self._cooldown_until = {}              # site -> 冷却结束时间戳
        self._review = defaultdict(list)       # site -> 待人工复核的任务

    def record(self, site, task, error, response=None):
        """记录一次失败并给出处置：原因、下一步动作、剩余次数、证据。"""
        result = classify(error, response)
        reason = result["reason"]
        pol = policy(reason)

        self._seq += 1
        entry = {
            "id": self._seq,
            "site": site,
            "task": task,
            "reason": reason,
            "evidence": result["evidence"],
            "ts": self._now(),
        }
        self._records[site].append(entry)

        if reason == REASON_BANNED:
            # 封禁：整站冷却，期间不再向该站点发请求。
            self._cooldown_until[site] = self._now() + pol["delay"]

        action = pol["action"]
        if action == "retry":
            if self._attempts[site] < self.site_budget:
                self._attempts[site] += 1
            else:
                # 站点预算耗尽，重试降级为放弃。
                action = "give_up"

        if action == "give_up":
            self._review[site].append({"task": task, "reason": reason,
                                       "evidence": result["evidence"]})

        return {
            "reason": reason,
            "action": action,
            "remaining": max(0, self.site_budget - self._attempts[site]),
            "delay": pol["delay"],
            "switch_exit": pol["switch_exit"],
            "evidence": result["evidence"],
            "trace_id": entry["id"],
        }

    def site_available(self, site):
        """封禁冷却结束自动恢复，不需要手工改配置。"""
        until = self._cooldown_until.get(site)
        return until is None or self._now() >= until

    def breakdown(self, site):
        """值班同学按站点查看失败构成，如 {"timeout": 3, "banned": 1}。"""
        return dict(Counter(r["reason"] for r in self._records[site]))

    def records(self, site, reason=None):
        """按站点（可选按原因）取失败记录，追溯到具体请求。"""
        recs = self._records[site]
        if reason is not None:
            recs = [r for r in recs if r["reason"] == reason]
        return list(recs)

    def review_list(self, site=None):
        """人工复核清单；不传 site 时返回全部站点。"""
        if site is not None:
            return list(self._review[site])
        return {s: list(items) for s, items in self._review.items()}

    def export_review(self, site):
        """按站点导出复核清单（JSON 行），供值班同学处理。"""
        return "\n".join(json.dumps({"site": site, **item}, ensure_ascii=False)
                         for item in self._review[site])

    def requeue(self, site, task):
        """复核通过后重新入队，不计入当天重试次数。

        返回 True 表示该任务在复核清单中并已移除。
        """
        items = self._review[site]
        for i, item in enumerate(items):
            if item["task"] == task:
                del items[i]
                return True
        return False

    def new_day(self):
        """跨天重置重试预算；失败记录和复核清单保留。"""
        self._attempts.clear()
