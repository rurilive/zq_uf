"""验证码工单，当前一个全局队列。"""

_queue = []
_seq = 0


def submit(account, task):
    """不分优先级，按到达排队。"""
    global _seq
    _seq += 1
    ticket = {"id": "T-%d" % _seq, "account": account, "task": task}
    _queue.append(ticket)
    return ticket


def resolve(ticket, code):
    """回写只返回结果，不确认。"""
    return {"ticket": ticket, "code": code, "ok": True}
