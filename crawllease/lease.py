"""采集任务租约。

规则：
- 领取带 TTL，记录持有者与到期时间；版本号每次发放/接管递增（fencing token）。
- 续约核对持有者：非持有者或已过期租约续约失败，到期后任务自动变为可接管。
- 接管仅允许针对已过期租约，记录原持有者，并写入可查询、可导出的历史。
- 释放幂等：重复释放、非持有者释放、释放不存在的任务都不抛异常。
- 等待者按到达顺序（FIFO）排队；释放或到期后队首立即获得租约。
- 到期前 warn_before 秒通过回调提醒持有者一次，续约后提醒窗口重置。

线程安全，纯内存、纯标准库；逻辑时钟可注入，便于确定性测试。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime

DEFAULT_TTL = 30.0
DEFAULT_WARN_BEFORE = 10.0
DEFAULT_REAPER_INTERVAL = 0.2

FREE = "free"
HELD = "held"
EXPIRED = "expired"
RELEASED = "released"


class _Record:
    __slots__ = ("task", "owner", "version", "expires_at", "state",
                 "ttl", "taken_over", "previous_owner", "warned")

    def __init__(self, task, owner, version, expires_at, ttl):
        self.task = task
        self.owner = owner
        self.version = version
        self.expires_at = expires_at
        self.state = HELD
        self.ttl = ttl
        self.taken_over = False
        self.previous_owner = None
        self.warned = False


class LeaseManager:
    """单进程内的任务租约管理器，可实例化多个互相隔离的实例。"""

    def __init__(self, ttl=DEFAULT_TTL, warn_before=DEFAULT_WARN_BEFORE,
                 clock=None, wallclock=None, on_expiring=None, on_expired=None,
                 auto_reaper=True, reaper_interval=DEFAULT_REAPER_INTERVAL):
        self.default_ttl = float(ttl)
        self.warn_before = float(warn_before)
        self._clock = clock or time.monotonic
        self._wallclock = wallclock or time.time
        self._on_expiring = on_expiring
        self._on_expired = on_expired
        self._auto_reaper = auto_reaper
        self._reaper_interval = reaper_interval

        self._cond = threading.Condition(threading.RLock())
        self._records = {}          # task -> _Record
        self._waiters = {}          # task -> deque[token]
        self._events = []           # 追加写的完整历史
        self._seq = 0
        self._wait_counter = 0
        self._reaper = None
        self._stopped = threading.Event()

    # ---------------------------------------------------------------- 内部工具

    def _signal(self, task):
        """状态变更后唤醒所有等待者，调用方须持锁。

        所有持锁状态变更都经此通知；等待循环用 wait 超时兜底并在每次
        唤醒后重检谓词，因此伪唤醒或锁竞争导致的漏唤醒不会造成永久等待。
        """
        self._cond.notify_all()

    def _ttl(self, ttl):
        return float(ttl) if ttl is not None else self.default_ttl

    def _ensure_reaper(self):
        if not self._auto_reaper or self._reaper is not None:
            return
        t = threading.Thread(target=self._reaper_loop,
                             name="crawllease-reaper", daemon=True)
        self._reaper = t
        t.start()

    def _reaper_loop(self):
        while not self._stopped.wait(self._reaper_interval):
            try:
                self.sweep()
            except Exception:
                # 后台线程不能因一次检查异常而退出
                pass

    def _grantable(self, task):
        """调用方须持锁。无记录或非 held 状态即可发放。"""
        rec = self._records.get(task)
        return rec is None or rec.state != HELD

    def _expire_if_due(self, task):
        """惰性过期：调用方须持锁。返回待锁外触发的回调列表。"""
        pending = []
        rec = self._records.get(task)
        if rec is not None and rec.state == HELD and self._clock() >= rec.expires_at:
            rec.state = EXPIRED
            self._append("expired", task, reason="ttl reached")
            self._signal(task)
            if self._on_expired is not None:
                pending.append((self._on_expired, (task, rec.owner, 0.0)))
        return pending

    def _append(self, event, task, owner=None, *, previous_owner=None,
                reason=None, ttl=None, taken_over=None):
        """追加一条历史事件，调用方须持锁。"""
        rec = self._records.get(task)
        now = self._clock()
        self._seq += 1
        self._events.append({
            "seq": self._seq,
            "time": datetime.fromtimestamp(self._wallclock()).isoformat(),
            "monotonic": now,
            "task": task,
            "event": event,
            "owner": owner if owner is not None else (rec.owner if rec else None),
            "previous_owner": previous_owner if previous_owner is not None
                              else (rec.previous_owner if rec else None),
            "version": rec.version if rec else 0,
            "state": rec.state if rec else FREE,
            "ttl": ttl if ttl is not None else (rec.ttl if rec else 0.0),
            "expires_in": round(max(0.0, rec.expires_at - now), 6)
                          if rec and rec.state == HELD else 0.0,
            "taken_over": taken_over if taken_over is not None
                          else (rec.taken_over if rec else False),
            "reason": reason,
        })
        return self._events[-1]

    def _envelope(self, task, *, granted=None, renewed=None, taken_over=None,
                  released=None, reason=None, queued=False,
                  queue_position=None, idempotent=None):
        """统一返回结构：持有者、剩余时间、续约/接管/释放结果都在这里。"""
        rec = self._records.get(task)
        now = self._clock()
        if rec is not None:
            state = rec.state
            if state == HELD:
                expires_in = max(0.0, rec.expires_at - now)
                if expires_in == 0.0:
                    state = EXPIRED
            else:
                # 非持有中（已释放/已过期）不存在剩余时间
                expires_in = 0.0
            owner = rec.owner
            version = rec.version
            ttl = rec.ttl
            warned = rec.warned
            prev = rec.previous_owner
            to = rec.taken_over
        else:
            state = FREE
            expires_in = 0.0
            owner = None
            version = 0
            ttl = 0.0
            warned = False
            prev = None
            to = False
        return {
            "task": task,
            "owner": owner,
            "state": state,
            "version": version,
            "ttl": ttl,
            "expires_in": round(expires_in, 6),
            "warned": warned,
            "granted": granted,
            "renewed": renewed,
            "taken_over": taken_over if taken_over is not None else to,
            "released": released,
            "previous_owner": prev,
            "queued": queued,
            "queue_position": queue_position,
            "queue_length": len(self._waiters.get(task, ())),
            "idempotent": idempotent,
            "reason": reason,
        }

    def _grant(self, task, owner, ttl, take_over, previous_owner):
        """发放租约（新领或接管），调用方须持锁。"""
        old = self._records.get(task)
        rec = _Record(task, owner,
                      old.version + 1 if old else 1,
                      self._clock() + ttl, ttl)
        rec.taken_over = take_over
        rec.previous_owner = previous_owner
        self._records[task] = rec
        self._append("taken_over" if take_over else "acquired", task, owner,
                     previous_owner=previous_owner, ttl=ttl,
                     taken_over=take_over)
        self._signal(task)
        return rec

    # ---------------------------------------------------------------- 对外操作

    def acquire(self, task, owner, ttl=None, wait=False, timeout=None,
                on_enqueue=None):
        """领取任务。

        空闲/已释放/已过期可立即领取（过期走接管路径）；他人持有中则拒绝，
        wait=True 时按 FIFO 排队，timeout 为排队最长等待秒数（None 表示一直等）。
        同一持有者重复领取是幂等 no-op，返回当前租约信息。
        on_enqueue 在成功入队后、进入阻塞等待前回调（测试同步用）。
        """
        ttl = self._ttl(ttl)
        self._ensure_reaper()
        pending = []
        with self._cond:
            pending += self._expire_if_due(task)
            rec = self._records.get(task)
            if rec is not None and rec.state == HELD and rec.owner == owner:
                self._append("acquired", task, owner, reason="already held")
                env = self._envelope(task, granted=True, renewed=False,
                                     reason="already held")
            elif self._grantable(task):
                take_over = rec is not None and rec.state == EXPIRED
                previous = rec.owner if take_over else None
                self._grant(task, owner, ttl, take_over, previous)
                env = self._envelope(task, granted=True, renewed=False,
                                     taken_over=take_over, reason=None)
            elif not wait:
                self._append("denied", task, owner, reason="held by another owner")
                env = self._envelope(
                    task, granted=False, renewed=False,
                    reason="held by another owner",
                    queue_position=len(self._waiters.get(task, ())) + 1)
            else:
                env = self._wait_in_queue(task, owner, ttl, timeout,
                                          on_enqueue)
        for cb, args in pending:
            cb(*args)
        return env

    # 无显式 timeout 时，wait 的兜底唤醒周期：即使通知彻底丢失也能自愈，
    # 正常路径下由 _signal 即时唤醒，不会真的等满。
    _WAIT_TICK = 1.0

    def _wait_in_queue(self, task, owner, ttl, timeout, on_enqueue):
        """FIFO 排队，调用方须持锁。"""
        q = self._waiters.setdefault(task, deque())
        self._wait_counter += 1
        token = (owner, self._wait_counter)
        q.append(token)
        if on_enqueue is not None:
            # 已在管理器锁内入队，回调返回前不会有其他状态变更
            on_enqueue()
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            current = self._waiters.get(task)
            if self._grantable(task) and current and current[0] is token:
                q = current
                break
            if deadline is None:
                # 有界 wait 兜底漏唤醒；正常由 _signal 即时唤醒
                self._cond.wait(self._WAIT_TICK)
                continue
            remaining = deadline - self._clock()
            if remaining <= 0.0:
                current.remove(token)
                self._append("wait_timeout", task, owner,
                             reason="wait timeout")
                self._signal(task)
                return self._envelope(
                    task, granted=False, renewed=False, queued=True,
                    reason="wait timeout",
                    queue_position=len(self._waiters.get(task, ())) + 1)
            # 不超过兜底周期，保证漏唤醒时也能及时重检
            self._cond.wait(min(remaining, self._WAIT_TICK))
        q.popleft()
        self._expire_if_due(task)
        rec = self._records.get(task)
        take_over = rec is not None and rec.state == EXPIRED
        previous = rec.owner if take_over else None
        self._grant(task, owner, ttl, take_over, previous)
        # 还有等待者时再通知一次，确保队首更替后被链式唤醒
        if self._waiters.get(task):
            self._signal(task)
        return self._envelope(task, granted=True, renewed=False,
                              taken_over=take_over, queued=True, reason=None)

    def renew(self, task, owner, ttl=None):
        """续约。仅当前持有者在租约未过期时可续约，否则 renewed=False。"""
        self._ensure_reaper()
        pending = []
        with self._cond:
            pending += self._expire_if_due(task)
            rec = self._records.get(task)
            if rec is not None and rec.state == HELD and rec.owner == owner:
                ttl = self._ttl(ttl)
                rec.expires_at = self._clock() + ttl
                rec.ttl = ttl
                rec.warned = False
                self._append("renewed", task, owner, ttl=ttl)
                env = self._envelope(task, granted=True, renewed=True)
            else:
                if rec is None or rec.state == RELEASED:
                    reason = "no active lease"
                elif rec.state == EXPIRED:
                    reason = "lease expired"
                else:
                    reason = "not the holder"
                self._append("renew_failed", task, owner, reason=reason)
                env = self._envelope(task, granted=False, renewed=False,
                                     reason=reason)
        for cb, args in pending:
            cb(*args)
        return env

    def release(self, task, owner):
        """主动释放，幂等：任何重复/非法释放都不抛异常。"""
        pending = []
        with self._cond:
            pending += self._expire_if_due(task)
            rec = self._records.get(task)
            if rec is None:
                self._append("release_skipped", task, owner,
                             reason="no active lease")
                return self._envelope(task, released=True, idempotent=True,
                                      reason="no active lease")
            if rec.state == RELEASED:
                self._append("release_skipped", task, owner,
                             reason="already released")
                return self._envelope(task, released=True, idempotent=True,
                                      reason="already released")
            if rec.state == EXPIRED:
                # 租约已过期、持有者已失效：释放视为幂等 no-op，
                # 但保留过期状态，任务仍只能通过接管领取。
                self._append("release_skipped", task, owner,
                             reason="lease expired")
                return self._envelope(task, released=True, idempotent=True,
                                      reason="lease expired")
            if rec.owner != owner:
                self._append("release_skipped", task, owner,
                             reason="not the holder")
                return self._envelope(task, released=False, idempotent=False,
                                      reason="not the holder")
            old_owner = rec.owner
            rec.state = RELEASED
            rec.owner = None
            rec.warned = False
            self._append("released", task, old_owner)
            self._signal(task)
            env = self._envelope(task, released=True, idempotent=False,
                                 reason="released")
        for cb, args in pending:
            cb(*args)
        return env

    def takeover(self, task, owner, ttl=None):
        """接管已过期租约，记录原持有者；活跃租约不允许强制接管。"""
        ttl = self._ttl(ttl)
        self._ensure_reaper()
        pending = []
        with self._cond:
            pending += self._expire_if_due(task)
            rec = self._records.get(task)
            if rec is None or rec.state != EXPIRED:
                self._append("denied", task, owner,
                             reason="no expired lease to take over")
                env = self._envelope(task, granted=False, taken_over=False,
                                     reason="lease is not expired")
            else:
                previous = rec.owner
                self._grant(task, owner, ttl, True, previous)
                env = self._envelope(task, granted=True, taken_over=True,
                                     reason=None)
        for cb, args in pending:
            cb(*args)
        return env

    def status(self, task):
        """按任务查看租约状态（含剩余时间、持有者、排队长度）。"""
        with self._cond:
            self._expire_if_due(task)
            return self._envelope(task)

    def sweep(self):
        """同步执行一次到期/临期检查，返回本次产生的事件（测试和无线程场景用）。"""
        pending = []
        emitted = []
        with self._cond:
            now = self._clock()
            for task in list(self._records):
                rec = self._records[task]
                if rec.state != HELD:
                    continue
                if not rec.warned and rec.expires_at - now <= self.warn_before:
                    rec.warned = True
                    left = max(0.0, rec.expires_at - now)
                    emitted.append(self._append(
                        "expiring", task, rec.owner, reason="lease expiring soon"))
                    if self._on_expiring is not None:
                        pending.append((self._on_expiring, (task, rec.owner, left)))
                if now >= rec.expires_at:
                    rec.state = EXPIRED
                    emitted.append(self._append("expired", task, rec.owner,
                                                reason="ttl reached"))
                    self._signal(task)
                    if self._on_expired is not None:
                        pending.append((self._on_expired, (task, rec.owner, 0.0)))
        for cb, args in pending:
            cb(*args)
        return emitted

    def history(self, task=None, event=None, owner=None, limit=None):
        """历史记录查询，可按任务/事件类型/持有者过滤，limit 取最近 N 条。"""
        with self._cond:
            out = []
            for ev in self._events:
                if task is not None and ev["task"] != task:
                    continue
                if event is not None and ev["event"] != event:
                    continue
                if owner is not None and ev["owner"] != owner:
                    continue
                out.append(dict(ev))
            if limit is not None:
                out = out[-int(limit):]
            return out

    def takeover_records(self, task=None):
        """仅返回接管记录（含原持有者），可导出、可回看。"""
        return self.history(task=task, event="taken_over")

    def export_takeovers(self, path=None, task=None):
        """导出接管记录为 JSON：给路径写文件并返回路径，否则返回 JSON 字符串。"""
        text = json.dumps(self.takeover_records(task),
                          ensure_ascii=False, indent=2)
        if path is None:
            return text
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def reset(self):
        """清空全部租约与历史，主要供测试使用。"""
        with self._cond:
            self._records.clear()
            self._waiters.clear()
            self._events.clear()
            self._seq = 0

    def shutdown(self):
        """停止后台到期检查线程。"""
        self._stopped.set()


# ---------------------------------------------------------------- 模块级默认实例

_default = LeaseManager()


def acquire(task, owner, ttl=None, wait=False, timeout=None,
            on_enqueue=None):
    return _default.acquire(task, owner, ttl=ttl, wait=wait,
                            timeout=timeout, on_enqueue=on_enqueue)


def renew(task, owner, ttl=None):
    return _default.renew(task, owner, ttl=ttl)


def release(task, owner):
    return _default.release(task, owner)


def takeover(task, owner, ttl=None):
    return _default.takeover(task, owner, ttl=ttl)


def status(task):
    return _default.status(task)


def sweep():
    return _default.sweep()


def history(task=None, event=None, owner=None, limit=None):
    return _default.history(task=task, event=event, owner=owner, limit=limit)


def takeover_records(task=None):
    return _default.takeover_records(task)


def export_takeovers(path=None, task=None):
    return _default.export_takeovers(path=path, task=task)


def reset_default():
    """重置模块级默认管理器（测试隔离用）。"""
    _default.reset()


def shutdown_default():
    _default.shutdown()
