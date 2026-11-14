"""验证码工单分派：按优先级派单，账号互斥，超时回收，回写确认。"""

import time

_queue = []      # 待处理工单，高优先级在前，同优先级按到达先后
_expired = []    # 超时回收记录
_resolved = []   # 回写确认记录
_seq = 0
_clock = time.monotonic  # 取时函数，测试可替换


class TicketError(Exception):
    """工单异常基类。"""


class AccountBusyError(TicketError):
    """同账号已有待处理工单。"""


class TicketStateError(TicketError):
    """工单状态不允许当前操作（重复回写 / 已超时回收）。"""


def _reclaim(now):
    """回收到期工单并释放账号，返回本次回收的工单。"""
    due = [t for t in _queue if now >= t["deadline"]]
    for t in due:
        t["status"] = "expired"
        _queue.remove(t)
        _expired.append({
            "id": t["id"], "account": t["account"], "task": t["task"],
            "deadline": t["deadline"], "reclaimed_at": now,
        })
    return due


def _find(records, tid):
    for r in records:
        if r["id"] == tid:
            return r
    return None


def submit(account, task, priority=0, timeout=60.0):
    """建单入队。priority 数值大的先派；同账号同时只允许一张待处理工单。"""
    if not account:
        raise TicketError("account 不能为空")
    if not isinstance(priority, (int, float)) or isinstance(priority, bool):
        raise TicketError("priority 必须是数值，当前为 %r" % (priority,))
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise TicketError("timeout 必须是正数秒，当前为 %r" % (timeout,))
    global _seq
    now = _clock()
    _reclaim(now)
    for t in _queue:
        if t["account"] == account:
            raise AccountBusyError(
                "账号 %r 已有待处理工单 %s，需先回写或等超时回收" % (account, t["id"]))
    _seq += 1
    ticket = {
        "id": "T-%d" % _seq,
        "account": account,
        "task": task,
        "priority": priority,
        "timeout": timeout,
        "created_at": now,
        "deadline": now + timeout,
        "status": "pending",
    }
    pos = len(_queue)
    for i, t in enumerate(_queue):
        if t["priority"] < priority:
            pos = i
            break
    _queue.insert(pos, ticket)
    return ticket


def resolve(ticket, code):
    """回写验证码并确认。每张工单只确认一次，重复回写或已超时工单抛错。"""
    now = _clock()
    _reclaim(now)
    tid = ticket["id"] if isinstance(ticket, dict) else ticket
    if not tid:
        raise TicketError("ticket 不能为空")
    if _find(_resolved, tid) is not None:
        raise TicketStateError("工单 %s 已确认过，不能重复回写" % tid)
    if _find(_expired, tid) is not None:
        raise TicketStateError("工单 %s 已超时回收，不能回写" % tid)
    pending = _find(_queue, tid)
    if pending is None:
        # 未登记的工单号：按人工线下回写记录，不阻断流程
        _resolved.append({"id": tid, "account": None, "code": code,
                          "ok": True, "confirmed": True, "source": "manual"})
        return {"ticket": tid, "code": code, "ok": True}
    pending["status"] = "resolved"
    _queue.remove(pending)
    _resolved.append({"id": tid, "account": pending["account"], "code": code,
                      "ok": True, "confirmed": True, "at": now})
    return {"ticket": tid, "code": code, "ok": True}


def queue_snapshot():
    """当前待处理队列（先触发一次超时回收）。"""
    _reclaim(_clock())
    return [dict(t) for t in _queue]


def expired_records():
    """超时回收记录。"""
    return list(_expired)


def resolve_records():
    """回写确认记录。"""
    return list(_resolved)
