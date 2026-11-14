"""采集任务的跨进程租约：文件锁保证原子性，栅栏令牌拒绝旧持有者写入。

租约状态持久化在 JSON 状态文件中（默认 $CRAWLLEASE_STATE 或临时目录），
所有 读-改-写 都在 fcntl.flock 排他文件锁内完成，因此多个进程同时操作
同一任务时，同一时刻只存在一个有效租约。每次 claim 都会把栅栏值（fence）
单调递增，旧持有者之后的 renew/release 会被拒绝。
"""

import contextlib
import json
import os
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 POSIX 平台退化为进程内锁
    fcntl = None


class LeaseError(Exception):
    """租约状态文件损坏、不可读或格式非法时抛出。"""


_STATE_ENV = "CRAWLLEASE_STATE"
_MAX_RECLAIMS = 1000

_leases = {}
_reclaims = []
_state_file = None
_lock = threading.Lock()


def _state_path():
    if _state_file:
        return _state_file
    return os.environ.get(_STATE_ENV) or os.path.join(
        tempfile.gettempdir(), "crawllease-state.json"
    )


@contextlib.contextmanager
def _locked():
    """进程内互斥锁 + 跨进程排他文件锁。"""
    with _lock:
        path = _state_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path + ".lock", "a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load():
    path = _state_path()
    if not os.path.exists(path):
        _leases.clear()
        _reclaims.clear()
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise LeaseError(f"租约状态文件损坏或不可读: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("leases"), dict):
        raise LeaseError(f"租约状态文件格式非法: {path}")
    _leases.clear()
    _leases.update(data["leases"])
    _reclaims.clear()
    _reclaims.extend(data.get("reclaims", []))


def _save():
    path = _state_path()
    tmp_path = path + ".tmp"
    payload = {"leases": _leases, "reclaims": _reclaims}
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        raise LeaseError(f"租约状态文件写入失败: {path}: {exc}") from exc


def _validate(task_id, owner, ttl=None):
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须是非空字符串")
    if not isinstance(owner, str) or not owner:
        raise ValueError("owner 必须是非空字符串")
    if ttl is not None and (not isinstance(ttl, (int, float)) or ttl < 0):
        raise ValueError("ttl 必须是非负数字，0 表示租约不过期")


def _is_expired(record, now):
    return record["expires_at"] is not None and record["expires_at"] <= now


def claim(task_id, owner, ttl):
    """领取租约，栅栏值单调递增；旧持有者（含过期前持有者）的写入随即失效。

    返回 {"task_id", "owner", "fence", "expires_at"}；发生持有者更替时
    记录一条回收记录（reason 为 expired 或 takeover）。
    """
    _validate(task_id, owner, ttl)
    now = time.time()
    with _locked():
        _load()
        previous = _leases.get(task_id)
        fence = (previous["fence"] if previous else 0) + 1
        if previous and previous["owner"] != owner:
            _reclaims.append(
                {
                    "task_id": task_id,
                    "previous_owner": previous["owner"],
                    "new_owner": owner,
                    "fence": fence,
                    "reason": "expired" if _is_expired(previous, now) else "takeover",
                    "at": now,
                }
            )
            del _reclaims[:-_MAX_RECLAIMS]
        record = {
            "task_id": task_id,
            "owner": owner,
            "fence": fence,
            "expires_at": now + ttl if ttl else None,
        }
        _leases[task_id] = record
        _save()
        return dict(record)


def renew(task_id, owner, ttl):
    """原子续期；非当前持有者或租约已过期时拒绝并返回 False。"""
    _validate(task_id, owner, ttl)
    now = time.time()
    with _locked():
        _load()
        record = _leases.get(task_id)
        if record is None or record["owner"] != owner or _is_expired(record, now):
            return False
        record["expires_at"] = now + ttl if ttl else None
        _save()
        return True


def release(task_id, owner):
    """原子释放；非当前持有者调用返回 False，不影响现有租约。"""
    _validate(task_id, owner)
    with _locked():
        _load()
        record = _leases.get(task_id)
        if record is None or record["owner"] != owner:
            return False
        del _leases[task_id]
        _save()
        return True


def status(task_id):
    """返回当前持有者、栅栏值和过期时间；无有效租约时返回 None。"""
    _validate(task_id, "probe")
    with _locked():
        _load()
        record = _leases.get(task_id)
        return dict(record) if record else None


def reclaims():
    """返回回收记录列表（旧持有者被替换的轨迹）。"""
    with _locked():
        _load()
        return [dict(item) for item in _reclaims]
