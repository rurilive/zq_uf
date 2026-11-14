"""可借还的父子速率配额网格。

父配额按权重切给子任务；空闲额度可被兄弟临时借用，
借用有上限和到期时间；归还、回收与父退出均为幂等。
"""

import time

DEFAULT_TTL = 60.0


def _now():
    return time.monotonic()


class _UsedLedger(dict):
    """父任务 -> 当前占用总额；clear 时连带清空网格状态。"""

    def clear(self):
        super().clear()
        _parents.clear()
        _children.clear()


class _Child:
    __slots__ = ("parent", "used_own", "lent", "borrows", "reclaimed")

    def __init__(self, parent):
        self.parent = parent
        self.used_own = 0   # 占用的自有额度
        self.lent = 0       # 借出给兄弟的额度
        self.borrows = []   # [借出方, 数量, 到期时刻]
        self.reclaimed = 0  # 被回收的借用笔数

    @property
    def borrowed_in(self):
        return sum(amount for _, amount, _ in self.borrows)


class _Parent:
    __slots__ = ("quota", "weights", "borrow_cap", "ttl", "children", "reclaimed")

    def __init__(self, quota, weights, borrow_cap, ttl):
        self.quota = quota
        self.weights = dict(weights or {})
        self.borrow_cap = borrow_cap
        self.ttl = ttl
        self.children = {}  # 子任务名 -> _Child
        self.reclaimed = 0


_used = _UsedLedger()
_parents = {}
_children = {}


def configure(parent, quota, weights=None, borrow_cap=None, ttl=DEFAULT_TTL):
    """设定父任务总额、子任务权重、借用上限与借用到期秒数。"""
    cfg = _parents.get(parent)
    if cfg is None:
        cfg = _Parent(quota, weights, borrow_cap, ttl)
        _parents[parent] = cfg
    else:
        cfg.quota = quota
        cfg.weights.update(weights or {})
        cfg.borrow_cap = borrow_cap
        cfg.ttl = ttl
    return cfg


def _parent_for(parent):
    cfg = _parents.get(parent)
    if cfg is None:
        cfg = _Parent(float("inf"), None, None, DEFAULT_TTL)
        _parents[parent] = cfg
    return cfg


def _child_for(cfg, parent, name):
    child = cfg.children.get(name)
    if child is None:
        child = _Child(parent)
        cfg.children[name] = child
        _children[name] = child
    return child


def _allotment(cfg, name):
    names = set(cfg.weights) | set(cfg.children)
    total = sum(cfg.weights.get(n, 1) for n in names)
    if total <= 0:
        return 0
    return cfg.quota * cfg.weights.get(name, 1) / total


def _borrow_cap(cfg, name):
    if cfg.borrow_cap is not None:
        return cfg.borrow_cap
    return _allotment(cfg, name)


def _reclaim_expired(cfg, parent, now):
    """回收到期借用；借用记录随回收移除，重复执行是空操作。"""
    for child in cfg.children.values():
        kept = []
        for owner, amount, expiry in child.borrows:
            if expiry <= now:
                cfg.children[owner].lent -= amount
                child.reclaimed += 1
                cfg.reclaimed += 1
                _used[parent] = max(0, _used.get(parent, 0) - amount)
            else:
                kept.append([owner, amount, expiry])
        child.borrows[:] = kept


def take(parent, child, n=1):
    """先占自有额度，不足时向空闲兄弟借用；返回父任务当前总占用。"""
    cfg = _parent_for(parent)
    now = _now()
    _reclaim_expired(cfg, parent, now)
    for name in cfg.weights:
        _child_for(cfg, parent, name)
    record = _child_for(cfg, parent, child)

    remaining = n
    own_avail = _allotment(cfg, child) - record.used_own - record.lent
    grant = min(remaining, max(0, own_avail))
    record.used_own += grant
    remaining -= grant

    if remaining > 0:
        room = _borrow_cap(cfg, child) - record.borrowed_in
        need = min(remaining, max(0, room))
        borrowed = need
        for name in sorted(cfg.children):
            if name == child or need <= 0:
                continue
            other = cfg.children[name]
            idle = _allotment(cfg, name) - other.used_own - other.lent
            loan = min(need, max(0, idle))
            if loan <= 0:
                continue
            other.lent += loan
            record.borrows.append([name, loan, now + cfg.ttl])
            need -= loan
        remaining -= borrowed - need

    _used[parent] = _used.get(parent, 0) + (n - remaining)
    return _used[parent]


def give_back(child, n=1):
    """先还借用再还自有；多还、重复还、未知子任务都是空操作。"""
    record = _children.get(child)
    if record is None:
        return dict(_used)
    parent = record.parent
    cfg = _parents[parent]
    _reclaim_expired(cfg, parent, _now())

    left = n
    for borrow in reversed(record.borrows):
        if left <= 0:
            break
        repay = min(borrow[1], left)
        borrow[1] -= repay
        cfg.children[borrow[0]].lent -= repay
        left -= repay
    record.borrows[:] = [b for b in record.borrows if b[1] > 0]

    repay_own = min(record.used_own, left)
    record.used_own -= repay_own
    left -= repay_own

    _used[parent] = max(0, _used.get(parent, 0) - (n - left))
    return dict(_used)


def close(parent):
    """父任务退出，回收全部子额度；返回回收的借用笔数，重复关闭返回 0。"""
    cfg = _parents.pop(parent, None)
    if cfg is None:
        return 0
    reclaimed = 0
    for name, child in cfg.children.items():
        for owner, amount, _ in child.borrows:
            cfg.children[owner].lent -= amount
            child.reclaimed += 1
            reclaimed += 1
        child.borrows[:] = []
        _children.pop(name, None)
    cfg.reclaimed += reclaimed
    _used.pop(parent, None)
    return reclaimed


def stats(parent):
    """输出父总额、总占用、回收笔数，以及各子额度、借入、借出、回收数。"""
    cfg = _parents.get(parent)
    if cfg is None:
        return {"parent": parent, "quota": None, "used": 0,
                "reclaimed": 0, "children": {}}
    _reclaim_expired(cfg, parent, _now())
    return {
        "parent": parent,
        "quota": cfg.quota,
        "used": _used.get(parent, 0),
        "reclaimed": cfg.reclaimed,
        "children": {
            name: {
                "allotment": _allotment(cfg, name),
                "used_own": child.used_own,
                "borrowed_in": child.borrowed_in,
                "lent_out": child.lent,
                "reclaimed": child.reclaimed,
            }
            for name, child in cfg.children.items()
        },
    }


def _reset():
    _used.clear()
