"""验证码工单：按优先级分派，账号互斥，超时回收可转派，回写确认一次。

口径：
- submit 入队，dispatch 分派优先级最高的工单（同优先级先到先派）。
- 同账号同时只有一张进行中（queued/assigned）的工单。
- 工单超过 timeout 未回写即被 sweep 回收，释放账号并进入站点冷却。
- 回收的工单可 reassign 转派（可换账号），新工单保留原工单历史。
- resolve 回写并与原任务对账，确认只继续一次，重复回写报错。
"""

import time
import urllib.parse

DEFAULT_TIMEOUT = 30.0  # 秒，工单从提交到回写的最长存活时间
DEFAULT_COOLDOWN = 5.0  # 秒，回写/回收后账号在同一站点的冷却时间

_tickets = {}     # 工单号 -> 工单
_queue = []       # 待分派工单号，分派时按优先级挑选
_writebacks = []  # 回写日志（含对账结果）
_cooldowns = {}   # (账号, 站点) -> 冷却截止时刻
_next_id = 0


class TicketError(Exception):
    """工单异常路径的明确错误。"""


def _clock(now):
    return time.time() if now is None else float(now)


def _site_of(task):
    site = task.get("site")
    if site:
        return str(site)
    url = task.get("url")
    if url:
        host = urllib.parse.urlparse(str(url)).netloc
        if host:
            return host
    return "default"


def _record(ticket, event, detail, now):
    ticket["history"].append({"at": now, "event": event, "detail": detail})


def _brief(t, now):
    return {"ticket": t["id"], "account": t["account"], "site": t["site"],
            "priority": t["priority"], "state": t["state"],
            "remaining": max(0.0, t["deadline"] - now), "parent": t["parent"]}


def _active_of(account):
    for t in _tickets.values():
        if t["account"] == account and t["state"] in ("queued", "assigned"):
            return t
    return None


def _check_account_free(account, site, now):
    busy = _active_of(account)
    if busy is not None:
        raise TicketError(
            "账号 %s 已有进行中的工单 #%d，同账号同时只有一张工单"
            % (account, busy["id"]))
    until = _cooldowns.get((account, site))
    if until is not None and until > now:
        raise TicketError(
            "账号 %s 在站点 %s 冷却中，剩余 %.1f 秒" % (account, site, until - now))


def _validate_number(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TicketError("%s 必须是数字，得到 %r" % (name, value))
    if float(value) < minimum:
        raise TicketError("%s 必须不小于 %s，得到 %r" % (name, minimum, value))
    return float(value)


def _new_ticket(account, task, now, parent=None, history=None):
    global _next_id
    _next_id += 1
    timeout = _validate_number("timeout", task.get("timeout", DEFAULT_TIMEOUT), 0.000001)
    t = {
        "id": _next_id,
        "account": account,
        "site": _site_of(task),
        "priority": task.get("priority", 0),
        "timeout": timeout,
        "cooldown": _validate_number(
            "cooldown", task.get("cooldown", DEFAULT_COOLDOWN), 0.0),
        "task": dict(task),
        "state": "queued",
        "created": now,
        "deadline": now + timeout,
        "code": None,
        "parent": parent,
        "history": history if history is not None else [],
    }
    _tickets[t["id"]] = t
    _queue.append(t["id"])
    return t


def _release(t, now):
    """释放账号：该账号在此站点进入冷却，按站点区分。"""
    _cooldowns[(t["account"], t["site"])] = now + t["cooldown"]


def submit(account, task, now=None):
    """提交工单：校验账号互斥与站点冷却，按优先级入队待分派。"""
    now = _clock(now)
    if not isinstance(account, str) or not account:
        raise TicketError("account 必须是非空字符串，得到 %r" % (account,))
    if not isinstance(task, dict):
        raise TicketError("task 必须是 dict，得到 %r" % (task,))
    priority = task.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, (int, float)):
        raise TicketError("priority 必须是数字，得到 %r" % (priority,))
    site = _site_of(task)
    _check_account_free(account, site, now)
    t = _new_ticket(account, task, now)
    _record(t, "submitted", "priority=%s timeout=%ss" % (priority, t["timeout"]), now)
    return {"ticket": t["id"], "account": account, "site": site,
            "priority": t["priority"], "timeout": t["timeout"],
            "queued": len(_queue)}


def dispatch(now=None):
    """分派队列中优先级最高的工单（同优先级先到先派），分派前先回收超时工单。"""
    now = _clock(now)
    sweep(now)
    if not _queue:
        raise TicketError("队列为空，没有可分派的工单")
    best = max((_tickets[i] for i in _queue),
               key=lambda t: (t["priority"], -t["created"]))
    best["state"] = "assigned"
    _queue.remove(best["id"])
    _record(best, "dispatched", "", now)
    return _brief(best, now)


def sweep(now=None):
    """回收超时工单：标记 expired、移出队列、释放账号并进入站点冷却。"""
    now = _clock(now)
    expired = []
    for t in list(_tickets.values()):
        if t["state"] in ("queued", "assigned") and t["deadline"] < now:
            t["state"] = "expired"
            if t["id"] in _queue:
                _queue.remove(t["id"])
            _release(t, now)
            _record(t, "expired", "deadline=%s" % t["deadline"], now)
            expired.append(_brief(t, now))
    return expired


def reassign(ticket, account=None, now=None):
    """把超时回收的工单转派（可换账号），新工单保留原工单历史。"""
    now = _clock(now)
    old = _tickets.get(ticket)
    if old is None:
        raise TicketError("未知工单 #%r，无法转派" % (ticket,))
    if old["state"] != "expired":
        raise TicketError("工单 #%d 状态为 %s，只有超时回收的工单才能转派"
                          % (old["id"], old["state"]))
    account = old["account"] if account is None else account
    _check_account_free(account, old["site"], now)
    t = _new_ticket(account, old["task"], now,
                    parent=old["id"], history=list(old["history"]))
    _record(t, "reassigned", "from=#%d" % old["id"], now)
    return {"ticket": t["id"], "parent": old["id"], "account": account,
            "site": t["site"], "priority": t["priority"],
            "queued": len(_queue), "history": list(t["history"])}


def _reconcile(task, code):
    """回写结果与原任务对账，返回问题列表（空表示对上）。"""
    problems = []
    if "expect" in task and code != str(task["expect"]):
        problems.append("期望 %r 实收 %r" % (task["expect"], code))
    if "code_len" in task and len(code) != int(task["code_len"]):
        problems.append("长度应为 %d 实为 %d" % (int(task["code_len"]), len(code)))
    kind = task.get("code_kind")
    if kind == "digit" and not code.isdigit():
        problems.append("应为纯数字")
    elif kind == "alnum" and not code.isalnum():
        problems.append("应为字母数字")
    return problems


def resolve(ticket, code, now=None):
    """回写验证码并与原任务对账；确认只继续一次，重复回写报错。"""
    now = _clock(now)
    t = _tickets.get(ticket)
    if t is None:
        raise TicketError("未知工单 #%r" % (ticket,))
    if t["state"] == "resolved":
        raise TicketError("工单 #%d 已回写确认，确认只继续一次" % t["id"])
    if t["state"] == "expired":
        raise TicketError("工单 #%d 已超时回收，请先 reassign 转派再回写" % t["id"])
    if not isinstance(code, str) or not code:
        raise TicketError("code 必须是非空字符串，得到 %r" % (code,))
    problems = _reconcile(t["task"], code)
    if problems:
        raise TicketError("工单 #%d 回写与原任务对不上账：%s"
                          % (t["id"], "；".join(problems)))
    t["state"] = "resolved"
    t["code"] = code
    if t["id"] in _queue:
        _queue.remove(t["id"])
    _release(t, now)
    _record(t, "resolved", "code=%s" % code, now)
    _writebacks.append({
        "ticket": t["id"], "account": t["account"], "site": t["site"],
        "code": code, "expect": t["task"].get("expect"),
        "matched": True, "at": now,
    })
    return {"ticket": t["id"], "resolved": True, "confirmed": True,
            "resume": t["parent"] is not None,
            "account": t["account"], "site": t["site"]}


def status(now=None):
    """输出当前队列、超时工单、回写日志和账号冷却。"""
    now = _clock(now)
    pending = sorted((_tickets[i] for i in _queue),
                     key=lambda t: (-t["priority"], t["created"]))
    return {
        "queue": [_brief(t, now) for t in pending],
        "assigned": [_brief(t, now) for t in _tickets.values()
                     if t["state"] == "assigned"],
        "expired": [_brief(t, now) for t in _tickets.values()
                    if t["state"] == "expired"],
        "writebacks": list(_writebacks),
        "cooldowns": [
            {"account": a, "site": s, "remaining": max(0.0, until - now)}
            for (a, s), until in sorted(_cooldowns.items()) if until > now
        ],
    }
