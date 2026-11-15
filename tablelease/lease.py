"""跨进程数据表写租约。

状态存放在租约目录（环境变量 ``TABLELEASE_DIR``，默认 ``<tmp>/tablelease``）：

- ``<table>.json``：租约状态 —— 持有者、到期时间、重入计数、FIFO 等待队列
- ``<table>.lock``：fcntl.flock 互斥文件，保证读-改-写跨进程原子
- ``audit.log``：抢占审计（JSON Lines）

仅依赖标准库；flock 为 POSIX 接口，限单机跨进程使用。
"""

import contextlib
import fcntl
import json
import os
import tempfile
import time
import uuid
from urllib.parse import quote, unquote

__all__ = ["acquire", "release", "status", "audit_log"]

POLL_INTERVAL = 0.05


def _lease_dir():
    return os.environ.get(
        "TABLELEASE_DIR",
        os.path.join(tempfile.gettempdir(), "tablelease"),
    )


def _fname(table):
    return quote(str(table), safe="")


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _audit(dirpath, event):
    """追加一条审计记录（JSON Lines），调用方需已持有该表的 flock。"""
    path = os.path.join(dirpath, "audit.log")
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def _locked(table):
    """持有 <table>.lock 的排他 flock，读入状态、退出时原子写回。"""
    dirpath = _lease_dir()
    os.makedirs(dirpath, exist_ok=True)
    base = os.path.join(dirpath, _fname(table))
    fd = os.open(base + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with open(base + ".json", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {"table": table, "holder": None, "waiters": []}
        yield state
        tmp = base + ".json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, base + ".json")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_state(table):
    """共享锁只读一份状态，供 status 跨进程查看。"""
    dirpath = _lease_dir()
    base = os.path.join(dirpath, _fname(table))
    try:
        fd = os.open(base + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    except FileNotFoundError:
        return {"table": table, "holder": None, "waiters": []}
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            with open(base + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"table": table, "holder": None, "waiters": []}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _clear_dead_holder(state, now, table, by):
    """持有者租约过期或进程已死则清除并写抢占审计，返回被抢占的持有者。"""
    holder = state["holder"]
    if holder is None:
        return None
    if now < holder["expires_at"] and _pid_alive(holder["pid"]):
        return None
    reason = "expired" if now >= holder["expires_at"] else "holder_dead"
    state["holder"] = None
    _audit(_lease_dir(), {
        "event": "preempt",
        "table": table,
        "prev_owner": holder["owner"],
        "prev_pid": holder["pid"],
        "reason": reason,
        "by": by,
        "at": now,
    })
    return holder


def _prune_waiters(state):
    """清掉进程已死的等待者（它们永远不会来认领租约）。"""
    state["waiters"] = [w for w in state["waiters"] if _pid_alive(w["pid"])]


def _grant_view(table, owner, state, *, reentrant, preempted, waited):
    holder = state["holder"]
    now = time.time()
    return {
        "table": table,
        "owner": owner,
        "granted": True,
        "reentrant": reentrant,
        "count": holder["count"],
        "holder": holder["owner"],
        "expires_in": max(0.0, holder["expires_at"] - now),
        "expires_at": holder["expires_at"],
        "waiters": [w["owner"] for w in state["waiters"]],
        "preempted": preempted,
        "waited": round(waited, 3),
    }


def acquire(table, owner, ttl):
    """获取表写租约，必要时进入 FIFO 等待队列阻塞等待。

    - 租约空闲且轮到本请求：发放，holder=owner，count=1，expires_at=now+ttl
    - 持有者是同 owner：重入，count+1 并在同一 flock 内原子续约
    - 持有者租约过期或进程死亡：抢占（写审计）后按队列顺序发放
    - 否则追加到等待队列尾部，轮询直到拿到租约
    """
    ttl = float(ttl)
    token = uuid.uuid4().hex
    pid = os.getpid()
    waited = 0.0
    while True:
        with _locked(table) as state:
            now = time.time()
            prev = _clear_dead_holder(state, now, table, by=owner)
            _prune_waiters(state)
            holder = state["holder"]
            if holder is not None and holder["owner"] == owner:
                holder["count"] += 1
                holder["expires_at"] = now + ttl
                holder["pid"] = pid
                holder["token"] = token
                return _grant_view(table, owner, state, reentrant=True,
                                   preempted=None, waited=waited)
            me = next((w for w in state["waiters"] if w["token"] == token), None)
            if holder is None and (not state["waiters"] or state["waiters"][0] is me):
                if me is not None:
                    state["waiters"].remove(me)
                state["holder"] = {
                    "owner": owner, "pid": pid, "token": token,
                    "acquired_at": now, "expires_at": now + ttl, "count": 1,
                }
                return _grant_view(table, owner, state, reentrant=False,
                                   preempted=prev["owner"] if prev else None,
                                   waited=waited)
            if me is None:
                state["waiters"].append({
                    "owner": owner, "pid": pid,
                    "token": token, "enqueued_at": now,
                })
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL


def release(table, owner):
    """释放一次租约：计数减一，归零才真正释放。

    幂等：未持有或已释放时返回 ``duplicate=True``，不改状态；
    非持有者释放返回 ``released=False``，状态不变，可安全重试。
    """
    with _locked(table) as state:
        _prune_waiters(state)
        waiters = [w["owner"] for w in state["waiters"]]
        holder = state["holder"]
        if holder is None:
            return {"table": table, "owner": owner, "released": False,
                    "duplicate": True, "remaining": 0,
                    "holder": None, "waiters": waiters}
        if holder["owner"] != owner:
            return {"table": table, "owner": owner, "released": False,
                    "duplicate": False, "remaining": holder["count"],
                    "holder": holder["owner"], "waiters": waiters,
                    "error": "held_by_other"}
        holder["count"] -= 1
        if holder["count"] > 0:
            return {"table": table, "owner": owner, "released": False,
                    "duplicate": False, "remaining": holder["count"],
                    "holder": holder["owner"], "waiters": waiters}
        state["holder"] = None
        return {"table": table, "owner": owner, "released": True,
                "duplicate": False, "remaining": 0,
                "holder": None, "waiters": waiters}


def _status_one(table):
    state = _read_state(table)
    now = time.time()
    h = state.get("holder")
    holder = None
    if h:
        holder = {
            "owner": h["owner"],
            "pid": h["pid"],
            "count": h["count"],
            "expires_in": max(0.0, h["expires_at"] - now),
            "expired": now >= h["expires_at"],
            "alive": _pid_alive(h["pid"]),
        }
    waiters = [{
        "owner": w["owner"],
        "pid": w["pid"],
        "waiting": round(now - w["enqueued_at"], 3),
        "alive": _pid_alive(w["pid"]),
    } for w in state.get("waiters", [])]
    return {"table": state.get("table", table), "holder": holder, "waiters": waiters}


def status(table=None):
    """跨进程查看租约状态；table 为 None 时列出所有表。"""
    if table is not None:
        return _status_one(table)
    dirpath = _lease_dir()
    out = []
    if os.path.isdir(dirpath):
        for name in sorted(os.listdir(dirpath)):
            if name.endswith(".json"):
                out.append(_status_one(unquote(name[:-5])))
    return out


def audit_log():
    """读取抢占审计（JSON Lines -> list）。"""
    path = os.path.join(_lease_dir(), "audit.log")
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
