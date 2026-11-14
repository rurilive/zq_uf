"""面向采集任务的共享站点许可名单。

名单落盘为 JSON，用 fcntl 文件锁保证多进程原子更新，各进程读到一致结果；
每条记录带原因、任务范围和到期时间，到期自动解除（惰性生效，写操作时清除）；
拉黑 / 人工解除 / 到期清除都写审计日志（JSONL），可按站点和任务查询。
"""

import contextlib
import fcntl
import json
import os
import threading
import time

_STORE_ENV = "SITEALLOW_STORE"
_DEFAULT_STORE = os.path.join("~", ".siteallow", "allowlist.json")

GLOBAL_TASK = "*"  # 任务范围：所有采集任务

_inprocess_lock = threading.Lock()


class StoreError(Exception):
    """名单存储或内容异常（文件损坏、读写失败、解除不存在的记录等）。"""


def _store_path():
    return os.path.expanduser(os.environ.get(_STORE_ENV, _DEFAULT_STORE))


def _lock_path():
    return _store_path() + ".lock"


def _audit_path():
    return _store_path() + ".audit.jsonl"


def _key(host, task):
    return "%s|%s" % (host, task or GLOBAL_TASK)


@contextlib.contextmanager
def _locked(exclusive):
    """进程内互斥 + 跨进程 flock；写操作用排他锁，读操作用共享锁。"""
    with _inprocess_lock:
        store_dir = os.path.dirname(_store_path())
        if store_dir:
            os.makedirs(store_dir, exist_ok=True)
        with open(_lock_path(), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _load():
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise StoreError("名单文件损坏: %s: %s" % (_store_path(), exc)) from exc
    except OSError as exc:
        raise StoreError("名单文件读取失败: %s: %s" % (_store_path(), exc)) from exc
    if not isinstance(data, dict):
        raise StoreError("名单文件格式非法（应为 JSON 对象）: %s" % _store_path())
    return data


def _save(entries):
    """先写临时文件再 os.replace，保证其他进程不会读到写了一半的名单。"""
    path = _store_path()
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise StoreError("名单写入失败: %s: %s" % (path, exc)) from exc
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _write_audit(event, host, reason, task, **extra):
    """追加一条审计记录。必须在排他锁内调用。"""
    record = {
        "ts": time.time(),
        "event": event,
        "host": host,
        "reason": reason,
        "task": task or GLOBAL_TASK,
        "pid": os.getpid(),
    }
    record.update(extra)
    try:
        with open(_audit_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise StoreError("审计日志写入失败: %s: %s" % (_audit_path(), exc)) from exc


def _purge_expired(entries, now):
    """清除到期记录并补写 expire 审计。必须在排他锁内调用。"""
    for key in [k for k, e in entries.items() if e["expires_at"] <= now]:
        entry = entries.pop(key)
        _write_audit("expire", entry["host"], entry["reason"],
                     None if entry["task"] == GLOBAL_TASK else entry["task"],
                     expires_at=entry["expires_at"])


def _validate_host(host):
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host 必须是非空字符串，收到: %r" % (host,))
    return host.strip()


def _validate_task(task):
    if task is None:
        return None
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task 必须是非空字符串或 None，收到: %r" % (task,))
    return task.strip()


def block(host, reason, ttl, task=None):
    """拉黑站点。ttl 为有效期秒数（必须为正）；task 缺省表示所有采集任务。"""
    host = _validate_host(host)
    task = _validate_task(task)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason 必须是非空字符串，收到: %r" % (reason,))
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        raise ValueError("ttl 必须是正数秒数，收到: %r" % (ttl,))
    now = time.time()
    entry = {
        "host": host,
        "reason": reason.strip(),
        "task": task or GLOBAL_TASK,
        "blocked_at": now,
        "expires_at": now + float(ttl),
    }
    with _locked(exclusive=True):
        entries = _load()
        _purge_expired(entries, now)
        entries[_key(host, task)] = entry
        _save(entries)
        _write_audit("block", host, entry["reason"], task,
                     ttl=float(ttl), expires_at=entry["expires_at"])
    _sync_mirror()
    return dict(entry)


def allowed(host, task=None):
    """站点当前是否允许抓取。到期记录视为已解除。"""
    host = _validate_host(host)
    task = _validate_task(task)
    now = time.time()
    with _locked(exclusive=False):
        entries = _load()
    _sync_mirror(entries)
    for entry in entries.values():
        if entry["host"] != host or entry["expires_at"] <= now:
            continue
        if entry["task"] == GLOBAL_TASK or entry["task"] == task:
            return False
    return True


def unblock(host, task=None, reason="manual"):
    """人工解除拉黑并写审计。task 缺省时解除该站点的所有范围记录。"""
    host = _validate_host(host)
    task = _validate_task(task)
    now = time.time()
    removed = []
    with _locked(exclusive=True):
        entries = _load()
        keys = [k for k, e in entries.items()
                if e["host"] == host and e["expires_at"] > now
                and (task is None or e["task"] == task)]
        if not keys:
            raise StoreError("站点无生效中的拉黑记录，无法解除: host=%r task=%r"
                             % (host, task or GLOBAL_TASK))
        _purge_expired(entries, now)
        for key in keys:
            if key in entries:
                removed.append(entries.pop(key))
        _save(entries)
        for entry in removed:
            _write_audit("unblock", host, reason,
                         None if entry["task"] == GLOBAL_TASK else entry["task"])
    _sync_mirror()
    return removed


def current(task=None, now=None):
    """当前生效的名单（未到期）。给定 task 时只列对该任务生效的记录。"""
    task = _validate_task(task)
    now = time.time() if now is None else now
    with _locked(exclusive=False):
        entries = _load()
    _sync_mirror(entries)
    out = []
    for entry in entries.values():
        if entry["expires_at"] <= now:
            continue
        if task is not None and entry["task"] not in (GLOBAL_TASK, task):
            continue
        out.append(dict(entry))
    out.sort(key=lambda e: (e["host"], e["task"]))
    return out


def audit(host=None, task=None):
    """查询审计记录，可按站点和任务过滤，按时间升序返回。"""
    if host is not None:
        host = _validate_host(host)
    task = _validate_task(task)
    try:
        with _locked(exclusive=False):
            with open(_audit_path(), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StoreError("审计日志读取失败: %s: %s" % (_audit_path(), exc)) from exc
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StoreError("审计日志损坏: %s: %s" % (_audit_path(), exc)) from exc
        if host is not None and record.get("host") != host:
            continue
        if task is not None and record.get("task") != task:
            continue
        records.append(record)
    return records


class _BlockedMirror(dict):
    """进程内镜像，仅供兼容查看；clear() 会同时清空落盘名单与审计日志。"""

    def clear(self):
        dict.clear(self)
        with _locked(exclusive=True):
            _save({})
            try:
                os.unlink(_audit_path())
            except FileNotFoundError:
                pass


_blocked = _BlockedMirror()


def _sync_mirror(entries=None):
    if entries is None:
        with _locked(exclusive=False):
            entries = _load()
    now = time.time()
    dict.clear(_blocked)
    _blocked.update({k: dict(v) for k, v in entries.items()
                     if v["expires_at"] > now})
