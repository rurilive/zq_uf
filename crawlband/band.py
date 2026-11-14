"""采集带宽配额：按任务限速，同组空闲额度可借还，到期自动回收。"""

import sys
import threading
import time
import types

RATE = 1024  # 全局总带宽，所有任务在用额度之和的上限

CONFIG = {
    "base_rate": RATE,    # 每任务基础速率（自有额度，随取随有）
    "burst_limit": RATE,  # 每任务突发上限（自有在用 + 借入未还）
    "borrow_limit": RATE,  # 每任务未还借入上限
    "borrow_ttl": 30.0,   # 借用到期时间（秒），到期自动归还出借方
    "groups": {},         # task -> 组名；缺省 "default"，借还限同组
}

_used = 0
_now = time.monotonic  # 时钟，测试可替换为假时钟

_lock = threading.RLock()  # 回收与归还嵌套调用（如 finish 内调 give_back）可重入
_tasks = {}

_DEFAULT_GROUP = "default"


class _Loan:
    __slots__ = ("lender", "amount", "expiry")

    def __init__(self, lender, amount, expiry):
        self.lender = lender
        self.amount = amount
        self.expiry = expiry


class _Task:
    __slots__ = ("name", "group", "own", "loans", "lent", "rejected")

    def __init__(self, name, group):
        self.name = name
        self.group = group
        self.own = 0       # 自有基础额度在用
        self.loans = []    # 借入未还 [_Loan]
        self.lent = {}     # 借出 {borrower: amount}
        self.rejected = 0  # 拒绝数

    @property
    def borrowed(self):
        return sum(loan.amount for loan in self.loans)

    @property
    def in_use(self):
        return self.own + self.borrowed


def _get_task(name):
    t = _tasks.get(name)
    if t is None:
        t = _tasks[name] = _Task(name, CONFIG["groups"].get(name, _DEFAULT_GROUP))
    return t


def _reap_locked(now):
    """收回到期借入，归还给出借方。可重入：重复调用不重复回收。"""
    global _used
    for t in list(_tasks.values()):
        kept = []
        for loan in t.loans:
            if loan.expiry <= now:
                lender = _tasks.get(loan.lender)
                if lender is not None:
                    left = lender.lent.get(t.name, 0) - loan.amount
                    if left > 0:
                        lender.lent[t.name] = left
                    else:
                        lender.lent.pop(t.name, None)
                _used -= loan.amount
            else:
                kept.append(loan)
        t.loans = kept


def take(task, amount):
    """按任务限速取额度：先用自有基础额度，不够向同组空闲任务借。

    超出突发上限、借入上限或全局总量即抛 RuntimeError 并计入拒绝数。
    """
    global _used
    if amount < 0:
        raise ValueError("amount must be >= 0")
    with _lock:
        now = _now()
        _reap_locked(now)
        t = _get_task(task)
        base = CONFIG["base_rate"]
        if t.in_use + amount > CONFIG["burst_limit"] or _used + amount > RATE:
            t.rejected += 1
            raise RuntimeError("bandwidth exceeded")
        own_part = min(amount, max(base - t.own, 0))
        rest = amount - own_part
        plan = []
        if rest:
            if t.borrowed + rest > CONFIG["borrow_limit"]:
                t.rejected += 1
                raise RuntimeError("bandwidth exceeded")
            got = 0
            for peer in list(_tasks.values()):
                if peer is t or peer.group != t.group:
                    continue
                idle = base - peer.own - sum(peer.lent.values())
                if idle <= 0:
                    continue
                piece = min(idle, rest - got)
                plan.append((peer.name, piece))
                got += piece
                if got >= rest:
                    break
            if got < rest:
                t.rejected += 1
                raise RuntimeError("bandwidth exceeded")
        expiry = now + CONFIG["borrow_ttl"]
        for lender_name, piece in plan:
            lender = _tasks[lender_name]
            lender.lent[task] = lender.lent.get(task, 0) + piece
            t.loans.append(_Loan(lender_name, piece, expiry))
        t.own += own_part
        _used += amount
        return amount


def give_back(task, amount):
    """归还额度：先还借入（先到期先还），再还自有。

    可重入：任务不存在或超还不报错，只清到 0。返回全局在用额度。
    """
    global _used
    if amount < 0:
        raise ValueError("amount must be >= 0")
    with _lock:
        _reap_locked(_now())
        t = _tasks.get(task)
        if t is None:
            return _used
        left = min(amount, t.in_use)
        for loan in sorted(t.loans, key=lambda l: l.expiry):
            if left <= 0:
                break
            piece = min(loan.amount, left)
            loan.amount -= piece
            left -= piece
            _used -= piece
            lender = _tasks.get(loan.lender)
            if lender is not None:
                remain = lender.lent.get(task, 0) - piece
                if remain > 0:
                    lender.lent[task] = remain
                else:
                    lender.lent.pop(task, None)
        t.loans = [loan for loan in t.loans if loan.amount > 0]
        if left > 0:
            piece = min(left, t.own)
            t.own -= piece
            _used -= piece
        return _used


def finish(task):
    """任务结束：归还全部借入与自有，并收回借出。可重复调用。"""
    global _used
    with _lock:
        t = _tasks.get(task)
        if t is None:
            return _used
        give_back(task, t.in_use)  # RLock，可重入
        for borrower_name in list(t.lent):
            borrower = _tasks.get(borrower_name)
            if borrower is None:
                continue
            kept = []
            for loan in borrower.loans:
                if loan.lender == task:
                    _used -= loan.amount
                else:
                    kept.append(loan)
            borrower.loans = kept
        t.lent.clear()
        del _tasks[task]
        return _used


def stats():
    """各任务当前速率（在用额度）、借入、借出与拒绝数。"""
    with _lock:
        _reap_locked(_now())
        return {
            name: {
                "rate": t.in_use,
                "own": t.own,
                "borrowed_in": t.borrowed,
                "lent_out": sum(t.lent.values()),
                "rejected": t.rejected,
            }
            for name, t in _tasks.items()
        }


def reset():
    """清空全部任务与额度状态（配置保留）。"""
    with _lock:
        _tasks.clear()
        globals()["_used"] = 0


class _BandModule(types.ModuleType):
    """兼容旧测试：band._used = 0 视为整体重置。"""

    def __setattr__(self, name, value):
        if name == "_used" and value == 0:
            reset()
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _BandModule
