"""验证码工单调度：优先级分派、账号互斥、超时回收、回写确认一次。"""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

QUEUED = "queued"        # 排队中
ACTIVE = "active"        # 已派单，等待回写
RESOLVED = "resolved"    # 回写确认完成
TIMEOUT = "timeout"      # 超时回收

DEFAULT_TIMEOUT = 30.0   # 派单后等待回写的秒数
DEFAULT_COOLDOWN = 60.0  # 超时后账号在同站点的冷却秒数


def _site_of(task):
    if task.get("site"):
        return task["site"]
    return urlparse(task.get("url") or "").netloc or "default"


class TicketSystem:
    """验证码工单调度器。

    - 同一账号同时只有一张活动工单（账号互斥），其余按优先级排队；
    - 活动工单超时未回写：释放账号、记录超时、账号在该站点进入冷却；
    - 冷却按账号+站点区分，到期自动恢复接单，值班同学可手动调整；
    - 回写需与工单期望内容匹配，确认只生效一次，重复回写不再触发；
    - 超时工单可转派其他账号重新排队；
    - 全部操作记入事件日志，可导出、可回放重建。
    """

    def __init__(self, now=None, timeout=DEFAULT_TIMEOUT, cooldown=DEFAULT_COOLDOWN):
        self._now = now or time.monotonic
        self.timeout = float(timeout)
        self.cooldown = float(cooldown)
        self._seq = 0
        self._tickets = {}      # id -> 工单
        self._queue = []        # 排队中的工单 id
        self._accounts = {}     # account -> {"current": id|None, "cooldowns": {site: until}}
        self.timeouts = []      # 超时记录
        self.reassigns = []     # 转派记录
        self.rejections = []    # 回写不匹配拒绝记录
        self._events = []       # 操作事件（回放用）

    # ---- 内部 ----

    def _account(self, account):
        return self._accounts.setdefault(account, {"current": None, "cooldowns": {}})

    def _ordered(self):
        """排队工单按（优先级降序、到达升序）排序。"""
        return sorted(self._queue,
                      key=lambda tid: (-self._tickets[tid]["priority"],
                                       self._tickets[tid]["created"], tid))

    def _position(self, tid):
        """排队位置（1 起），不在队列中为 0。"""
        try:
            return self._ordered().index(tid) + 1
        except ValueError:
            return 0

    def _log(self, op, **params):
        self._events.append({"seq": len(self._events) + 1, "ts": self._now(),
                             "op": op, "params": params})

    def _sweep(self):
        now = self._now()
        # 超时回收：释放账号、记录超时、账号在该站点进入冷却
        for t in list(self._tickets.values()):
            if t["status"] == ACTIVE and t["deadline"] <= now:
                t["status"] = TIMEOUT
                t["finished"] = now
                acc = self._account(t["account"])
                if acc["current"] == t["id"]:
                    acc["current"] = None
                until = now + self.cooldown
                acc["cooldowns"][t["site"]] = until
                self.timeouts.append({
                    "ticket": t["id"], "account": t["account"], "site": t["site"],
                    "priority": t["priority"], "deadline": t["deadline"],
                    "ts": now, "cooldown_until": until,
                })
        # 冷却到期自动恢复接单
        for acc in self._accounts.values():
            for site, until in list(acc["cooldowns"].items()):
                if until <= now:
                    del acc["cooldowns"][site]
        # 按优先级给空闲且未冷却的账号派单
        for tid in self._ordered():
            t = self._tickets[tid]
            acc = self._account(t["account"])
            if acc["current"] is not None or t["site"] in acc["cooldowns"]:
                continue
            self._queue.remove(tid)
            t["status"] = ACTIVE
            t["deadline"] = now + t["ttl"]
            acc["current"] = tid

    def _view(self, t):
        return {"ticket": t["id"], "account": t["account"], "site": t["site"],
                "url": t["url"], "priority": t["priority"], "status": t["status"],
                "position": self._position(t["id"]),
                "created": t["created"], "deadline": t["deadline"],
                "finished": t["finished"]}

    # ---- 工单操作 ----

    def submit(self, account, task):
        """提交工单：入队后立即尝试派单，返回排队位置与账号占用情况。"""
        self._sweep()
        self._seq += 1
        t = {"id": self._seq, "account": account, "site": _site_of(task),
             "url": task.get("url"), "priority": int(task.get("priority", 0)),
             "code": task.get("code"), "ttl": float(task.get("timeout", self.timeout)),
             "status": QUEUED, "created": self._now(),
             "deadline": None, "finished": None}
        self._tickets[t["id"]] = t
        self._queue.append(t["id"])
        self._log("submit", account=account, task=dict(task))
        self._sweep()
        acc = self._account(account)
        return {"ticket": t["id"], "account": account, "site": t["site"],
                "priority": t["priority"], "status": t["status"],
                "position": self._position(t["id"]), "queued": len(self._queue),
                "account_busy": acc["current"] is not None,
                "cooldown_until": acc["cooldowns"].get(t["site"])}

    def resolve(self, ticket, code):
        """人工回写验证码：匹配才确认恢复，且只生效一次。"""
        self._sweep()
        t = self._tickets.get(ticket)
        if t is None:
            return {"ticket": ticket, "resolved": False, "resume": False,
                    "reason": "not_found"}
        if t["status"] == RESOLVED:
            return {"ticket": ticket, "resolved": True, "resume": False,
                    "duplicated": True}
        if t["status"] != ACTIVE:
            return {"ticket": ticket, "resolved": False, "resume": False,
                    "reason": t["status"]}
        self._log("resolve", ticket=ticket, code=code)
        if t["code"] is not None and code != t["code"]:
            self.rejections.append({"ticket": ticket, "account": t["account"],
                                    "ts": self._now(), "reason": "code_mismatch"})
            return {"ticket": ticket, "resolved": False, "resume": False,
                    "reason": "code_mismatch"}
        t["status"] = RESOLVED
        t["finished"] = self._now()
        acc = self._account(t["account"])
        if acc["current"] == t["id"]:
            acc["current"] = None
        self._sweep()
        return {"ticket": ticket, "resolved": True, "resume": True,
                "account": t["account"], "next": acc["current"]}

    def reassign(self, ticket, account=None, priority=None):
        """超时工单转派：重新排队，可换账号、可改优先级。"""
        self._sweep()
        t = self._tickets.get(ticket)
        if t is None:
            return {"ticket": ticket, "reassigned": False, "reason": "not_found"}
        if t["status"] != TIMEOUT:
            return {"ticket": ticket, "reassigned": False, "reason": t["status"]}
        old = t["account"]
        if account:
            t["account"] = account
        if priority is not None:
            t["priority"] = int(priority)
        t["status"] = QUEUED
        t["deadline"] = None
        t["finished"] = None
        self._queue.append(t["id"])
        record = {"ticket": t["id"], "from": old, "to": t["account"],
                  "priority": t["priority"], "ts": self._now()}
        self.reassigns.append(record)
        self._log("reassign", ticket=ticket, account=account, priority=priority)
        self._sweep()
        return {"ticket": t["id"], "reassigned": True, "from": old,
                "to": t["account"], "status": t["status"],
                "position": self._position(t["id"])}

    # ---- 值班调整 ----

    def adjust_priority(self, ticket, priority, by="duty"):
        """值班同学手动调整排队工单优先级。"""
        self._sweep()
        t = self._tickets.get(ticket)
        if t is None:
            return {"ticket": ticket, "adjusted": False, "reason": "not_found"}
        if t["status"] != QUEUED:
            return {"ticket": ticket, "adjusted": False, "reason": t["status"]}
        old = t["priority"]
        t["priority"] = int(priority)
        self._log("adjust_priority", ticket=ticket, priority=int(priority), by=by)
        self._sweep()
        return {"ticket": ticket, "adjusted": True, "from": old,
                "to": t["priority"], "by": by, "position": self._position(ticket)}

    def set_cooldown(self, account, site, seconds):
        """值班同学手动设置/解除账号在某站点的冷却，seconds<=0 解除。"""
        acc = self._account(account)
        seconds = float(seconds)
        if seconds <= 0:
            acc["cooldowns"].pop(site, None)
            until = None
        else:
            until = self._now() + seconds
            acc["cooldowns"][site] = until
        self._log("set_cooldown", account=account, site=site, seconds=seconds)
        self._sweep()
        return {"account": account, "site": site, "cooldown_until": until}

    # ---- 查询 ----

    def account_view(self, account):
        """按账号查看：当前工单、排队位置、冷却剩余、超时与转派记录。"""
        self._sweep()
        acc = self._account(account)
        now = self._now()
        current = acc["current"]
        queued = [{"ticket": tid, "position": i + 1,
                   "priority": self._tickets[tid]["priority"],
                   "site": self._tickets[tid]["site"]}
                  for i, tid in enumerate(self._ordered())
                  if self._tickets[tid]["account"] == account]
        return {
            "account": account,
            "status": "busy" if current is not None else "idle",
            "accepting": current is None,
            "current": self._view(self._tickets[current]) if current is not None else None,
            "queued": queued,
            "cooldowns": {site: round(until - now, 3)
                          for site, until in acc["cooldowns"].items()},
            "timeouts": [dict(r) for r in self.timeouts if r["account"] == account],
            "reassigns": [dict(r) for r in self.reassigns
                          if r["from"] == account or r["to"] == account],
        }

    def ticket_status(self, ticket):
        """单张工单状态，含排队位置与账号占用情况。"""
        self._sweep()
        t = self._tickets.get(ticket)
        if t is None:
            return {"ticket": ticket, "status": "not_found"}
        view = self._view(t)
        acc = self._account(t["account"])
        view["account_busy"] = acc["current"] is not None
        view["cooldown_until"] = acc["cooldowns"].get(t["site"])
        return view

    # ---- 导出与回放 ----

    def export(self):
        """导出工单、超时/转派/拒绝记录与操作事件。"""
        self._sweep()
        return {"tickets": [self._view(t) for t in self._tickets.values()],
                "timeouts": [dict(r) for r in self.timeouts],
                "reassigns": [dict(r) for r in self.reassigns],
                "rejections": [dict(r) for r in self.rejections],
                "events": [dict(e) for e in self._events]}

    def export_json(self, path=None):
        text = json.dumps(self.export(), ensure_ascii=False, indent=2)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        return text

    @classmethod
    def replay(cls, events, timeout=DEFAULT_TIMEOUT, cooldown=DEFAULT_COOLDOWN):
        """按导出的事件日志回放，重建调度过程。"""
        clock = {"t": 0.0}
        system = cls(now=lambda: clock["t"], timeout=timeout, cooldown=cooldown)
        for ev in events:
            clock["t"] = ev["ts"]
            p = ev["params"]
            if ev["op"] == "submit":
                system.submit(p["account"], p["task"])
            elif ev["op"] == "resolve":
                system.resolve(p["ticket"], p["code"])
            elif ev["op"] == "reassign":
                system.reassign(p["ticket"], p.get("account"), p.get("priority"))
            elif ev["op"] == "adjust_priority":
                system.adjust_priority(p["ticket"], p["priority"], p.get("by", "duty"))
            elif ev["op"] == "set_cooldown":
                system.set_cooldown(p["account"], p["site"], p["seconds"])
        return system


_default = TicketSystem()


def reset(now=None, **kwargs):
    """重置模块级默认调度器（测试与演示用）。"""
    global _default
    _default = TicketSystem(now=now, **kwargs)
    return _default


def submit(account, task):
    return _default.submit(account, task)


def resolve(ticket, code):
    return _default.resolve(ticket, code)
