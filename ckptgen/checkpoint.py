"""多代际检查点：原子写、保留清理、损坏回退、并发串行化。"""
import json
import os
import re
import tempfile
import threading
import zlib

ROOT = "checkpoints"
KEEP = 3

_FILE_RE = re.compile(r"gen-(\d+)\.json$")

_locks_guard = threading.Lock()
_task_locks = {}
_reading_guard = threading.Lock()
_reading = {}
_fallbacks_guard = threading.Lock()
_fallbacks = []


def _task_dir(task, root=None):
    return os.path.join(root or ROOT, task)


def _path(task, generation, root=None):
    return os.path.join(_task_dir(task, root), "gen-%06d.json" % generation)


def _lock_for(task, root=None):
    key = (os.path.abspath(root or ROOT), task)
    with _locks_guard:
        return _task_locks.setdefault(key, threading.Lock())


def _generations(task, root=None):
    """现存代际号，新到旧排序。"""
    try:
        names = os.listdir(_task_dir(task, root))
    except FileNotFoundError:
        return []
    found = []
    for name in names:
        match = _FILE_RE.match(name)
        if match:
            found.append(int(match.group(1)))
    return sorted(found, reverse=True)


def _encode(generation, state):
    body = json.dumps(state, ensure_ascii=False, sort_keys=True)
    payload = {
        "generation": generation,
        "checksum": "%08x" % (zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF),
        "state": state,
    }
    return json.dumps(payload, ensure_ascii=False)


def _read_payload(path):
    """读出并校验一代，损坏抛 ValueError。"""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    body = json.dumps(payload["state"], ensure_ascii=False, sort_keys=True)
    checksum = "%08x" % (zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF)
    if payload.get("checksum") != checksum:
        raise ValueError("checksum mismatch: " + path)
    return payload


def _atomic_replace(src, dst):
    os.replace(src, dst)


def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_acquire(key):
    with _reading_guard:
        _reading[key] = _reading.get(key, 0) + 1


def _read_release(key):
    with _reading_guard:
        count = _reading.get(key, 0)
        if count <= 1:
            _reading.pop(key, None)
        else:
            _reading[key] = count - 1


def _record_fallback(task, generation, fell_back_to, reason):
    with _fallbacks_guard:
        _fallbacks.append({
            "task": task,
            "generation": generation,
            "fell_back_to": fell_back_to,
            "reason": str(reason),
        })


def _prune(task, keep, root=None):
    """超出保留数按最旧优先清理，正在读的代际跳过。"""
    root_abs = os.path.abspath(root or ROOT)
    for generation in _generations(task, root)[keep:]:
        key = (root_abs, task, generation)
        with _reading_guard:
            busy = _reading.get(key, 0) > 0
        if busy:
            continue
        try:
            os.unlink(_path(task, generation, root))
        except FileNotFoundError:
            pass
    return _generations(task, root)


def save(task, state, root=None, keep=KEEP):
    """原子写入新一代，返回当前代际号、保留列表和文件路径。"""
    with _lock_for(task, root):
        existing = _generations(task, root)
        generation = existing[0] + 1 if existing else 1
        directory = _task_dir(task, root)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(_encode(generation, state))
                handle.flush()
                os.fsync(handle.fileno())
            path = _path(task, generation, root)
            _atomic_replace(tmp, path)
            _fsync_dir(directory)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {
            "task": task,
            "generation": generation,
            "kept": _prune(task, keep, root),
            "path": path,
        }


def load(task, generation=None, root=None):
    """generation=1 读最新代，2 读上一代；损坏代际跳过并回退到更旧一代。"""
    available = _generations(task, root)
    if not available:
        raise FileNotFoundError("no checkpoint for task: " + task)
    index = (generation or 1) - 1
    if index < 0 or index >= len(available):
        raise FileNotFoundError(
            "generation %r not found for task: %s" % (generation, task))
    root_abs = os.path.abspath(root or ROOT)
    candidates = available[index:]
    for position, current in enumerate(candidates):
        key = (root_abs, task, current)
        _read_acquire(key)
        try:
            return _read_payload(_path(task, current, root))["state"]
        except FileNotFoundError:
            continue  # 被并发清理，试更旧一代
        except (ValueError, KeyError) as error:
            fell_back_to = (candidates[position + 1]
                            if position + 1 < len(candidates) else None)
            _record_fallback(task, current, fell_back_to, error)
        finally:
            _read_release(key)
    raise FileNotFoundError("no usable checkpoint for task: " + task)


def generations(task, root=None):
    """保留列表：现存代际号，新到旧排序。"""
    return _generations(task, root)


def fallbacks(task=None):
    """回退记录：损坏代际号、回退到哪一代、原因。"""
    with _fallbacks_guard:
        records = list(_fallbacks)
    if task is not None:
        records = [record for record in records if record["task"] == task]
    return records
