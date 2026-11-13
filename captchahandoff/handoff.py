"""登录验证码接管：等待队列 + 工单超时 + 回写继续 + 账号互斥。"""

from __future__ import annotations

import itertools
import threading
import time

DEFAULT_TICKET_TTL = 300.0
WAIT_POLL_INTERVAL = 0.5

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_EXPIRED = "expired"


class CaptchaRequired(Exception):
    """命中验证码：已生成工单暂停账号，或当前无法回写（无工单/已超时）。"""

    def __init__(self, account, kind="image", ticket_id=None, status=STATUS_PENDING, reason=None):
        self.account = account
        self.kind = kind
        self.ticket_id = ticket_id
        self.status = status
        super().__init__(reason or f"{account} 需要 {kind} 验证码，工单 {ticket_id} 待人工处理")


class CaptchaTimeout(TimeoutError):
    """工单超时未处理，已标记失败并释放账号。"""

    def __init__(self, account, ticket_id):
        self.account = account
        self.ticket_id = ticket_id
        super().__init__(f"工单 {ticket_id}（{account}）超时未处理，已标记失败并释放账号")


class _Ticket:
    __slots__ = ("ticket_id", "account", "kind", "status", "code", "created_at", "deadline")

    def __init__(self, ticket_id, account, kind, ttl, now):
        self.ticket_id = ticket_id
        self.account = account
        self.kind = kind
        self.status = STATUS_PENDING
        self.code = None
        self.created_at = now
        self.deadline = now + ttl

    def snapshot(self):
        return {
            "ticket_id": self.ticket_id,
            "account": self.account,
            "kind": self.kind,
            "status": self.status,
            "code": self.code,
            "created_at": self.created_at,
            "deadline": self.deadline,
        }


_lock = threading.RLock()
_tickets = {}
_active_by_account = {}
_seq = itertools.count(1)


def _expire_locked(ticket, now):
    if ticket.status == STATUS_PENDING and now >= ticket.deadline:
        ticket.status = STATUS_EXPIRED
        if _active_by_account.get(ticket.account) == ticket.ticket_id:
            del _active_by_account[ticket.account]


def reset():
    """清空全部工单与账号占用（测试/演示用）。"""
    with _lock:
        _tickets.clear()
        _active_by_account.clear()


def challenge(account, kind="image", ttl=None):
    """命中验证码：生成工单、暂停账号并抛 CaptchaRequired。

    等待期内同账号重复调用只抛回同工单，不重复登录。
    """
    if not account:
        raise ValueError("account 不能为空")
    if ttl is None:
        ttl = DEFAULT_TICKET_TTL
    if ttl <= 0:
        raise ValueError(f"ttl 必须为正数，收到 {ttl!r}")
    now = time.monotonic()
    with _lock:
        active_id = _active_by_account.get(account)
        if active_id is not None:
            ticket = _tickets[active_id]
            _expire_locked(ticket, now)
            if ticket.status == STATUS_PENDING:
                raise CaptchaRequired(
                    account,
                    kind=ticket.kind,
                    ticket_id=ticket.ticket_id,
                    reason=f"{account} 已在等待工单 {ticket.ticket_id}，等待期内不重复登录",
                )
        ticket_id = f"T-{next(_seq):04d}"
        ticket = _Ticket(ticket_id, account, kind, ttl, now)
        _tickets[ticket_id] = ticket
        _active_by_account[account] = ticket_id
        raise CaptchaRequired(account, kind=kind, ticket_id=ticket_id)


def submit(account, code):
    """人工回写验证码：工单置 resolved、释放账号并返回结果，原流程继续。

    空码或占位码（全部相同字符，如 0000）视为无效输入，抛 CaptchaRequired，
    不消费工单。
    """
    if not account:
        raise ValueError("account 不能为空")
    if not code:
        raise CaptchaRequired(
            account, status=None, reason=f"{account} 提交的验证码为空，无法回写"
        )
    if len(set(code)) == 1:
        raise CaptchaRequired(
            account,
            status=None,
            reason=f"{account} 提交的验证码 {code!r} 是占位码，视为无效输入",
        )
    now = time.monotonic()
    with _lock:
        active_id = _active_by_account.get(account)
        if active_id is None:
            raise CaptchaRequired(
                account, status=None, reason=f"{account} 没有待处理的验证码工单，无法回写"
            )
        ticket = _tickets[active_id]
        _expire_locked(ticket, now)
        if ticket.status != STATUS_PENDING:
            raise CaptchaRequired(
                account,
                kind=ticket.kind,
                ticket_id=ticket.ticket_id,
                status=ticket.status,
                reason=f"工单 {ticket.ticket_id} 已超时失败，账号已释放，请重新登录",
            )
        ticket.status = STATUS_RESOLVED
        ticket.code = code
        del _active_by_account[account]
        return ticket.snapshot()


def wait(account, poll_interval=WAIT_POLL_INTERVAL):
    """阻塞等待账号工单被回写；超时抛 CaptchaTimeout。"""
    if not account:
        raise ValueError("account 不能为空")
    if poll_interval <= 0:
        raise ValueError(f"poll_interval 必须为正数，收到 {poll_interval!r}")
    with _lock:
        active_id = _active_by_account.get(account)
    if active_id is None:
        raise CaptchaRequired(
            account, status=None, reason=f"{account} 没有待处理的验证码工单，无法等待"
        )
    while True:
        now = time.monotonic()
        with _lock:
            ticket = _tickets[active_id]
            _expire_locked(ticket, now)
            if ticket.status == STATUS_RESOLVED:
                return ticket.snapshot()
            if ticket.status == STATUS_EXPIRED:
                raise CaptchaTimeout(account, active_id)
            remaining = ticket.deadline - now
        time.sleep(max(min(poll_interval, remaining), 0.01))


def ticket_status(ticket_id):
    """查询单个工单快照；未知工单抛 KeyError。"""
    with _lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"未知工单 {ticket_id!r}")
        _expire_locked(ticket, time.monotonic())
        return ticket.snapshot()


def tickets():
    """返回全部工单快照列表。"""
    now = time.monotonic()
    with _lock:
        for ticket in _tickets.values():
            _expire_locked(ticket, now)
        return [ticket.snapshot() for ticket in _tickets.values()]
