"""全局重试配额协调。

一个重试许可（permit）同时占用站点名额和全局名额：

* 站点配额：同一 ``site`` 同时持有的许可数不超过 ``site_limit``；
* 全局配额：所有站点同时持有的许可总数不超过 ``global_limit``。

容量不足时请求进入优先级队列，任一许可释放、失败或超时归还后，
协调器会按优先级（高值优先，同优先级先到先得）补发排队任务。

对外接口保持 ``request(task, site)`` / ``release(task)``。
``release`` 只释放该任务最早持有的一个许可；重复释放、释放已经超时的
许可都是幂等的，不会二次扣减任何名额。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import uuid

try:  # 文件态跨进程查看/协调需要；非 POSIX 平台退化为进程内状态。
    import fcntl
except ImportError:  # pragma: no cover - 仅非 POSIX 平台
    fcntl = None

__all__ = [
    "QuotaCoordinator",
    "configure",
    "reset",
    "request",
    "release",
    "fail",
    "sweep_expired",
    "set_priority",
    "get_coordinator",
    "snapshot",
    "events",
    "read_state",
    "DEFAULT_GLOBAL_LIMIT",
    "DEFAULT_SITE_LIMIT",
    "DEFAULT_LEASE_SECONDS",
]

DEFAULT_GLOBAL_LIMIT = int(os.environ.get("RETRYQUOTA_GLOBAL_LIMIT", "64"))
DEFAULT_SITE_LIMIT = int(os.environ.get("RETRYQUOTA_SITE_LIMIT", "16"))
DEFAULT_LEASE_SECONDS = float(os.environ.get("RETRYQUOTA_LEASE_SECONDS", "300"))
DEFAULT_STATE_PATH = os.environ.get("RETRYQUOTA_STATE_PATH") or None
DEFAULT_EVENT_LIMIT = 256
DEFAULT_EXPIRED_MEMORY = 256


# ---------------------------------------------------------------------------
# 状态结构
# ---------------------------------------------------------------------------


def _new_state(
    global_limit,
    site_limit,
    site_limits,
    lease_seconds,
    priorities,
    now,
):
    return {
        "version": 1,
        "updated_at": now,
        "global_limit": None if global_limit is None else int(global_limit),
        "site_limit": None if site_limit is None else int(site_limit),
        "site_limits": {
            str(site): int(limit) for site, limit in (site_limits or {}).items()
        },
        "lease_seconds": None if lease_seconds is None else float(lease_seconds),
        "active": [],
        "queue": [],
        "priorities": {
            str(task): int(priority) for task, priority in (priorities or {}).items()
        },
        "failed": {},
        "expired_tasks": [],
        "seq": 0,
        "events": [],
    }


def _normalise_state(state):
    """补齐旧文件/手工文件里可能缺失的字段。"""
    defaults = _new_state(None, None, None, None, None, 0.0)
    for key, value in defaults.items():
        state.setdefault(key, value)
    for permit in state["active"]:
        permit.setdefault("claimed", True)
    return state


# ---------------------------------------------------------------------------
# 文件锁与序列化
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _file_lock(path, exclusive=True):
    if fcntl is None:
        yield
        return

    lock_path = str(path) + ".lock"
    parent = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(parent, exist_ok=True)
    with open(lock_path, "a+") as handle:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle.fileno(), mode)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _normalise_state(json.load(handle))
    except (FileNotFoundError, ValueError, TypeError):
        return None


def _save_state(path, state):
    path = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".retryquota-", dir=parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_state(path):
    """读取任意进程写入的协调状态文件（共享锁保护的一致性快照）。

    只读取磁盘上的原始状态，不会推进协调（不归还超时许可、不补发队列）；
    需要观察推进后的视图时，用同一 ``state_path`` 构造协调器再 ``snapshot()``。
    """
    with _file_lock(path, exclusive=False):
        state = _load_state(path)
    if state is None:
        return None
    return state


# ---------------------------------------------------------------------------
# 协调器
# ---------------------------------------------------------------------------


class QuotaCoordinator:
    """站点 + 全局两级重试配额协调器。

    参数
    ----
    global_limit:
        全局同时持有的许可上限；``None`` 表示只受站点上限约束。
    site_limit:
        每个站点默认的同时持有许可上限；``None`` 表示站点不设上限。
    site_limits:
        按站点覆盖 ``site_limit``，例如 ``{"shop": 2}``。
    lease_seconds:
        许可租期。超过租期仍未 ``release`` 的许可会自动归还；``None``
        表示永不超时。
    priorities:
        任务初始优先级；高值先出队。之后可用 :meth:`set_priority` 调整。
    state_path:
        可选的 JSON 状态文件。设置后，每次变更都在 ``flock`` 保护下
        加载-修改-原子写回，多个进程可共享同一份协调状态；任意进程也可
        通过 :func:`read_state` 或 ``python -m retryquota`` 查看。
        状态文件一旦创建，其中的配额/租期就是权威值；本参数只用于首次创建。
    clock:
        可注入时钟（默认进程内用 ``time.monotonic``，文件态用
        ``time.time``），便于测试租期。
    """

    def __init__(
        self,
        global_limit=DEFAULT_GLOBAL_LIMIT,
        site_limit=DEFAULT_SITE_LIMIT,
        site_limits=None,
        lease_seconds=DEFAULT_LEASE_SECONDS,
        priorities=None,
        state_path=None,
        clock=None,
        event_limit=DEFAULT_EVENT_LIMIT,
    ):
        self.global_limit = global_limit
        self.site_limit = site_limit
        self.site_limits = dict(site_limits or {})
        self.lease_seconds = lease_seconds
        self.priorities = dict(priorities or {})
        self.state_path = os.fspath(state_path) if state_path else None
        self.event_limit = int(event_limit)
        if clock is None:
            clock = time.time if self.state_path else time.monotonic
        self._clock = clock
        self._state = None
        if self.state_path is None:
            self._state = _new_state(
                self.global_limit,
                self.site_limit,
                self.site_limits,
                self.lease_seconds,
                self.priorities,
                self._now(),
            )

    # -- 基础设施 ---------------------------------------------------------

    def _now(self):
        return float(self._clock())

    def _mutate(self, action):
        if self.state_path is None:
            return action(self._state, self._now())

        with _file_lock(self.state_path, exclusive=True):
            state = _load_state(self.state_path)
            if state is None:
                state = _new_state(
                    self.global_limit,
                    self.site_limit,
                    self.site_limits,
                    self.lease_seconds,
                    self.priorities,
                    self._now(),
                )
            now = self._now()
            result = action(state, now)
            state["updated_at"] = now
            _save_state(self.state_path, state)
            return result

    def _site_cap(self, state, site):
        if site in state["site_limits"]:
            return state["site_limits"][site]
        return state["site_limit"]

    def _global_cap(self, state):
        return state["global_limit"]

    def _global_full(self, state):
        cap = self._global_cap(state)
        return cap is not None and len(state["active"]) >= cap

    def _site_full(self, state, site):
        cap = self._site_cap(state, site)
        return cap is not None and self._site_count(state, site) >= cap

    @staticmethod
    def _site_count(state, site):
        return sum(1 for permit in state["active"] if permit["site"] == site)

    @staticmethod
    def _task_round(state, task):
        return sum(1 for permit in state["active"] if permit["task"] == task)

    def _blocked_reason(self, state, site):
        site_full = self._site_full(state, site)
        global_full = self._global_full(state)
        if site_full and global_full:
            return "site+global"
        if site_full:
            return "site"
        if global_full:
            return "global"
        return "queued"

    def _event(self, state, kind, now, **fields):
        event = {"event": kind, "at": now}
        event.update(fields)
        state["events"].append(event)
        if len(state["events"]) > self.event_limit:
            del state["events"][: len(state["events"]) - self.event_limit]

    def _remember_expired(self, state, task):
        remembered = state["expired_tasks"]
        if task not in remembered:
            remembered.append(task)
        if len(remembered) > DEFAULT_EXPIRED_MEMORY:
            del remembered[: len(remembered) - DEFAULT_EXPIRED_MEMORY]

    def _forget_expired(self, state, task):
        try:
            state["expired_tasks"].remove(task)
        except ValueError:
            pass

    # -- 许可/队列 --------------------------------------------------------

    def _sweep(self, state, now):
        """归还已超时许可，返回超时许可列表（不补发队列）。"""
        expired = []
        remaining = []
        for permit in state["active"]:
            expires_at = permit.get("expires_at")
            if expires_at is not None and expires_at <= now:
                expired.append(permit)
                self._remember_expired(state, permit["task"])
                self._event(
                    state,
                    "expire",
                    now,
                    task=permit["task"],
                    site=permit["site"],
                    permit_id=permit["permit_id"],
                )
            else:
                remaining.append(permit)
        state["active"] = remaining
        return expired

    def _grant_from_queue(self, state, now):
        """按优先级补发队列，返回本次新发放的许可摘要。"""
        granted = []
        while True:
            if self._global_full(state):
                break
            chosen = None
            # 高优先级先出队；同优先级按请求序号 FIFO。
            for entry in sorted(
                state["queue"], key=lambda item: (-item["priority"], item["seq"])
            ):
                if not self._site_full(state, entry["site"]):
                    chosen = entry
                    break
            if chosen is None:
                break

            state["queue"].remove(chosen)
            permit = {
                "permit_id": uuid.uuid4().hex,
                "request_id": chosen["request_id"],
                "task": chosen["task"],
                "site": chosen["site"],
                "priority": chosen["priority"],
                "seq": chosen["seq"],
                "granted_at": now,
                "expires_at": (
                    None
                    if state["lease_seconds"] is None
                    else now + float(state["lease_seconds"])
                ),
                # 由别人释放/超时触发的补发，持有任务还没从自己的 request
                # 里看到这张许可；它下次 request 时直接复用并置 claimed。
                "claimed": False,
            }
            state["active"].append(permit)
            self._forget_expired(state, chosen["task"])
            self._event(
                state,
                "grant",
                now,
                task=chosen["task"],
                site=chosen["site"],
                permit_id=permit["permit_id"],
                source="queue",
            )
            granted.append(self._summary(permit))
        return granted

    def _dispatch(self, state, now):
        return self._grant_from_queue(state, now)

    @staticmethod
    def _summary(permit):
        return {
            "permit_id": permit["permit_id"],
            "request_id": permit["request_id"],
            "task": permit["task"],
            "site": permit["site"],
            "priority": permit["priority"],
            "granted_at": permit["granted_at"],
            "expires_at": permit["expires_at"],
        }

    @staticmethod
    def _queue_position(state, request_id):
        ordered = sorted(
            state["queue"], key=lambda item: (-item["priority"], item["seq"])
        )
        for index, entry in enumerate(ordered, start=1):
            if entry["request_id"] == request_id:
                return index
        return None

    # -- 公共 API ---------------------------------------------------------

    def request(self, task, site):
        """申请一个重试许可；超额时进入队列并返回排队信息。"""
        task = str(task)
        site = str(site)

        def action(state, now):
            expired = self._sweep(state, now)
            state["failed"].pop(task, None)
            dispatched = self._grant_from_queue(state, now)
            granted = [
                item
                for item in dispatched
                if item["task"] != task or item["site"] != site
            ]
            priority = int(state["priorities"].get(task, 0))

            # 同一任务在同一站点重复申请时复用已有排队条目，避免重试循环
            # 把队列撑爆、后续一次释放补发多个许可。
            existing = next(
                (
                    item
                    for item in state["queue"]
                    if item["task"] == task and item["site"] == site
                ),
                None,
            )
            if existing is not None:
                position = self._queue_position(state, existing["request_id"])
                return {
                    "task": task,
                    "site": site,
                    "granted": False,
                    "queued": True,
                    "reason": "already_queued",
                    "blocked_by": self._blocked_reason(state, site),
                    "position": position,
                    "queue_position": position,
                    "permit_id": None,
                    "priority": existing["priority"],
                    "granted_at": None,
                    "expires_at": None,
                    "round": self._task_round(state, task),
                    "queue_length": len(state["queue"]),
                    "expired_permits": len(expired),
                    "granted_from_queue": granted,
                }

            # 该任务此前排的条目若刚好被本轮补发，直接复用这张许可。
            reused = next(
                (
                    item
                    for item in state["active"]
                    if item["task"] == task
                    and item["site"] == site
                    and not item["claimed"]
                ),
                None,
            )
            if reused is not None:
                reused["claimed"] = True
                granted = [
                    item for item in granted if item["permit_id"] != reused["permit_id"]
                ]
                return {
                    "task": task,
                    "site": site,
                    "granted": True,
                    "queued": False,
                    "reason": "granted_from_queue",
                    "position": None,
                    "queue_position": None,
                    "permit_id": reused["permit_id"],
                    "priority": reused["priority"],
                    "granted_at": reused["granted_at"],
                    "expires_at": reused["expires_at"],
                    "round": self._task_round(state, task),
                    "queue_length": len(state["queue"]),
                    "expired_permits": len(expired),
                    "granted_from_queue": granted,
                }

            state["seq"] += 1
            entry = {
                "request_id": uuid.uuid4().hex,
                "task": task,
                "site": site,
                "priority": priority,
                "seq": state["seq"],
                "enqueued_at": now,
            }
            # 先入队再统一补发：新请求的优先级也参与本轮排序。
            state["queue"].append(entry)
            dispatched = self._grant_from_queue(state, now)
            # 只汇报"因这次调用而被补发的其它排队任务"，不含本次请求自身。
            granted = [
                item for item in dispatched if item["request_id"] != entry["request_id"]
            ]
            permit = next(
                (
                    item
                    for item in state["active"]
                    if item["request_id"] == entry["request_id"]
                ),
                None,
            )
            round_ = self._task_round(state, task)

            if permit is not None:
                permit["claimed"] = True
                return {
                    "task": task,
                    "site": site,
                    "granted": True,
                    "queued": False,
                    "reason": "granted",
                    "position": None,
                    "queue_position": None,
                    "permit_id": permit["permit_id"],
                    "priority": priority,
                    "granted_at": permit["granted_at"],
                    "expires_at": permit["expires_at"],
                    "round": round_,
                    "queue_length": len(state["queue"]),
                    "expired_permits": len(expired),
                    "granted_from_queue": granted,
                }

            blocked_by = self._blocked_reason(state, site)
            position = self._queue_position(state, entry["request_id"])
            self._event(
                state,
                "enqueue",
                now,
                task=task,
                site=site,
                position=position,
                blocked_by=blocked_by,
            )
            return {
                "task": task,
                "site": site,
                "granted": False,
                "queued": True,
                "reason": "queued",
                "blocked_by": blocked_by,
                "position": position,
                "queue_position": position,
                "permit_id": None,
                "priority": priority,
                "granted_at": None,
                "expires_at": None,
                "round": round_,
                "queue_length": len(state["queue"]),
                "expired_permits": len(expired),
                "granted_from_queue": granted,
            }

        return self._mutate(action)

    def release(self, task):
        """释放该任务最早持有的一个许可；重复/超时释放幂等。"""
        task = str(task)

        def action(state, now):
            expired = self._sweep(state, now)
            candidates = [
                permit for permit in state["active"] if permit["task"] == task
            ]
            if not candidates:
                expired_hit = task in state["expired_tasks"] or any(
                    permit["task"] == task for permit in expired
                )
                if expired_hit:
                    self._remember_expired(state, task)
                granted = self._dispatch(state, now)
                result = {
                    "task": task,
                    "released": False,
                    "duplicate": not expired_hit,
                    "expired": expired_hit,
                    "stale": expired_hit,
                    "reason": (
                        "expired_release" if expired_hit else "duplicate_release"
                    ),
                    "site": None,
                    "permit_id": None,
                    "round": self._task_round(state, task),
                    "queue_length": len(state["queue"]),
                    "expired_permits": len(expired),
                    "granted_from_queue": granted,
                }
                self._event(
                    state,
                    "release_rejected",
                    now,
                    task=task,
                    reason=result["reason"],
                )
                return result

            # 同一任务可能同时持有多个许可，按获取顺序释放最早的一个。
            permit = min(candidates, key=lambda item: (item["granted_at"], item["seq"]))
            state["active"].remove(permit)
            self._forget_expired(state, task)
            self._event(
                state,
                "release",
                now,
                task=task,
                site=permit["site"],
                permit_id=permit["permit_id"],
            )
            granted = self._dispatch(state, now)
            return {
                "task": task,
                "site": permit["site"],
                "released": True,
                "duplicate": False,
                "expired": False,
                "stale": False,
                "reason": "released",
                "permit_id": permit["permit_id"],
                "round": self._task_round(state, task),
                "queue_length": len(state["queue"]),
                "expired_permits": len(expired),
                "granted_from_queue": granted,
            }

        return self._mutate(action)

    def fail(self, task):
        """任务最终失败：归还它的全部许可并撤销排队请求，不占名额。"""
        task = str(task)

        def action(state, now):
            expired = self._sweep(state, now)
            held = [permit for permit in state["active"] if permit["task"] == task]
            for permit in held:
                state["active"].remove(permit)
                self._event(
                    state,
                    "fail_release",
                    now,
                    task=task,
                    site=permit["site"],
                    permit_id=permit["permit_id"],
                )
            cancelled = [entry for entry in state["queue"] if entry["task"] == task]
            for entry in cancelled:
                state["queue"].remove(entry)
                self._event(state, "cancel", now, task=task, site=entry["site"])
            state["failed"][task] = now
            self._forget_expired(state, task)
            granted = self._dispatch(state, now)
            return {
                "task": task,
                "failed": True,
                "released": len(held),
                "cancelled": len(cancelled),
                "sites": sorted({permit["site"] for permit in held}),
                "round": 0,
                "queue_length": len(state["queue"]),
                "expired_permits": len(expired),
                "granted_from_queue": granted,
            }

        return self._mutate(action)

    def sweep_expired(self, now=None):
        """显式归还超时许可，并补发排队任务。"""

        def action(state, now_):
            moment = self._now() if now is None else float(now)
            expired = self._sweep(state, moment)
            granted = self._dispatch(state, moment)
            return {
                "expired": [self._summary(permit) for permit in expired],
                "expired_count": len(expired),
                "granted_from_queue": granted,
                "active_total": len(state["active"]),
                "queue_length": len(state["queue"]),
            }

        return self._mutate(action)

    def set_priority(self, task, priority):
        """设置任务优先级；已排队条目立即按新优先级排序。"""
        task = str(task)
        priority = int(priority)

        def action(state, now):
            state["priorities"][task] = priority
            for entry in state["queue"]:
                if entry["task"] == task:
                    entry["priority"] = priority
            return {
                "task": task,
                "priority": priority,
                "queue_length": len(state["queue"]),
            }

        return self._mutate(action)

    def snapshot(self):
        """返回可跨进程查看的协调状态视图。

        查看本身也会推进协调：先归还超时许可，再把空出来的名额补发给
        排队任务，因此任何一次观察都能看到自动归还/出队后的真实状态。
        """
        def action(state, now):
            expired = self._sweep(state, now)
            self._dispatch(state, now)
            queue = sorted(
                state["queue"], key=lambda item: (-item["priority"], item["seq"])
            )
            sites = {}
            for permit in state["active"]:
                info = sites.setdefault(
                    permit["site"],
                    {
                        "active": 0,
                        "limit": self._site_cap(state, permit["site"]),
                        "queued": 0,
                    },
                )
                info["active"] += 1
            for entry in queue:
                info = sites.setdefault(
                    entry["site"],
                    {
                        "active": 0,
                        "limit": self._site_cap(state, entry["site"]),
                        "queued": 0,
                    },
                )
                info["queued"] += 1

            return {
                "pid": os.getpid(),
                "updated_at": now,
                "limits": {
                    "global": self._global_cap(state),
                    "site": state["site_limit"],
                    "site_limits": dict(state["site_limits"]),
                    "lease_seconds": state["lease_seconds"],
                },
                "active_total": len(state["active"]),
                "queued_total": len(queue),
                "active": [
                    {
                        "permit_id": permit["permit_id"],
                        "task": permit["task"],
                        "site": permit["site"],
                        "priority": permit["priority"],
                        "granted_at": permit["granted_at"],
                        "expires_at": permit["expires_at"],
                        "claimed": permit.get("claimed", True),
                    }
                    for permit in state["active"]
                ],
                "queue": [
                    {
                        "position": index,
                        "request_id": entry["request_id"],
                        "task": entry["task"],
                        "site": entry["site"],
                        "priority": entry["priority"],
                        "enqueued_at": entry["enqueued_at"],
                        "blocked_by": self._blocked_reason(state, entry["site"]),
                    }
                    for index, entry in enumerate(queue, start=1)
                ],
                "sites": sites,
                "task_rounds": {
                    task: self._task_round(state, task)
                    for task in sorted(
                        {permit["task"] for permit in state["active"]}
                    )
                },
                "priorities": dict(state["priorities"]),
                "failed": sorted(state["failed"]),
                "expired_count_just_now": len(expired),
                "events": list(state["events"]),
            }

        return self._mutate(action)

    def events(self, limit=None):
        """返回协调事件（grant/enqueue/release/expire/fail...）。"""
        def action(state, now):
            self._sweep(state, now)
            self._dispatch(state, now)
            items = list(state["events"])
            return items[-limit:] if limit is not None else items

        return self._mutate(action)


# ---------------------------------------------------------------------------
# 模块级默认协调器：保持 request(task, site) / release(task) 接口
# ---------------------------------------------------------------------------


def _make_default(**kwargs):
    options = {
        "global_limit": DEFAULT_GLOBAL_LIMIT,
        "site_limit": DEFAULT_SITE_LIMIT,
        "lease_seconds": DEFAULT_LEASE_SECONDS,
        "state_path": DEFAULT_STATE_PATH,
    }
    options.update(kwargs)
    return QuotaCoordinator(**options)


_default = _make_default()


def get_coordinator():
    """返回当前模块级协调器。"""
    return _default


def configure(**kwargs):
    """重建模块级协调器（会清空旧状态），参数同 QuotaCoordinator。"""
    global _default
    _default = _make_default(**kwargs)
    return _default


def reset(**kwargs):
    """``configure`` 的别名：清空状态并恢复/设置配额。"""
    return configure(**kwargs)


def request(task, site):
    return _default.request(task, site)


def release(task):
    return _default.release(task)


def fail(task):
    return _default.fail(task)


def sweep_expired(now=None):
    return _default.sweep_expired(now=now)


def set_priority(task, priority):
    return _default.set_priority(task, priority)


def snapshot():
    return _default.snapshot()


def events(limit=None):
    return _default.events(limit=limit)
