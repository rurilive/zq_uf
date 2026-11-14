"""跨进程租约锁。

锁状态（持有者、过期时间、重入计数、等待队列、抢占记录）持久化在
按表名分片的锁文件里，读写全程用 fcntl.flock 互斥，保证多进程下原子。
"""

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
from urllib.parse import quote

_DEFAULT_DIR = os.path.join(tempfile.gettempdir(), "tablelock")


def _lock_dir():
    return os.environ.get("TABLELOCK_DIR", _DEFAULT_DIR)


def _shard_path(table):
    """锁文件按表名分片：<dir>/<sha1前2位>/<表名>.lock"""
    digest = hashlib.sha1(table.encode("utf-8")).hexdigest()
    name = quote(table, safe="") or digest
    return os.path.join(_lock_dir(), digest[:2], name + ".lock")


def _empty_state():
    return {"owner": None, "expires_at": 0.0, "count": 0,
            "waiters": [], "preemptions": []}


def _load(fd):
    fd.seek(0)
    raw = fd.read()
    if not raw.strip():
        return _empty_state()
    state = _empty_state()
    state.update(json.loads(raw))
    return state


def _save(fd, state):
    fd.seek(0)
    fd.truncate()
    json.dump(state, fd)
    fd.flush()
    os.fsync(fd.fileno())


def _open(path, create):
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    return os.fdopen(os.open(path, flags, 0o644), "r+")


def acquire(table, owner, ttl):
    """获取租约锁。同一表同一时刻只有一个写者。

    - 锁空闲或已过期：获得锁；过期被抢占会追加抢占记录。
    - 持有者是自已：重入，计数 +1，并在同一把文件锁内原子续期。
    - 持有者未过期：失败，调用者按到达顺序进入等待队列。
    """
    path = _shard_path(table)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = time.time()
    with _open(path, create=True) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = _load(fd)
        holder = state["owner"]
        preempted = None

        if holder is None or state["expires_at"] <= now:
            if holder is not None:
                preempted = {"prev_owner": holder, "new_owner": owner,
                             "at": now, "reason": "expired"}
                state["preemptions"].append(preempted)
            state["owner"] = owner
            state["expires_at"] = now + ttl
            state["count"] = 1
            ok = True
        elif holder == owner:
            state["count"] += 1
            state["expires_at"] = now + ttl  # 原子续期
            ok = True
        else:
            ok = False

        if ok:
            state["waiters"] = [w for w in state["waiters"] if w["owner"] != owner]
        elif owner not in [w["owner"] for w in state["waiters"]]:
            state["waiters"].append({"owner": owner, "enqueued_at": now})

        _save(fd, state)
        result = {"ok": ok, "owner": state["owner"], "table": table,
                  "count": state["count"], "expires_at": state["expires_at"],
                  "queue": [w["owner"] for w in state["waiters"]]}
        if preempted:
            result["preempted"] = preempted
        return result


def release(table, owner):
    """释放一次持有。幂等：未持有（含已过期被抢占）返回 False，不报错。"""
    path = _shard_path(table)
    if not os.path.exists(path):
        return False
    with _open(path, create=False) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = _load(fd)
        if state["owner"] != owner:
            return False
        state["count"] -= 1
        if state["count"] <= 0:
            state["owner"] = None
            state["expires_at"] = 0.0
            state["count"] = 0
        _save(fd, state)
        return True


def status(table):
    """输出持有者、等待队列（按排队顺序）和抢占记录。"""
    path = _shard_path(table)
    if not os.path.exists(path):
        return {"table": table, "owner": None, "count": 0, "expires_at": None,
                "expired": False, "queue": [], "preemptions": []}
    with _open(path, create=False) as fd:
        fcntl.flock(fd, fcntl.LOCK_SH)
        state = _load(fd)
    expired = state["owner"] is not None and state["expires_at"] <= time.time()
    return {"table": table, "owner": state["owner"], "count": state["count"],
            "expires_at": state["expires_at"] or None, "expired": expired,
            "queue": [w["owner"] for w in state["waiters"]],
            "preemptions": list(state["preemptions"])}


class _LockRegistry(dict):
    """旧接口遗留的进程内视图；clear() 会连同锁目录一起清空（测试复位用）。"""

    def clear(self):
        super().clear()
        shutil.rmtree(_lock_dir(), ignore_errors=True)


_locks = _LockRegistry()
