"""多代际检查点：原子写、代际保留、损坏回退、并发安全。"""

import contextlib
import hashlib
import hmac
import json
import os
import threading

try:
    import fcntl
except ImportError:  # 非 POSIX 平台退化为仅进程内锁
    fcntl = None

ROOT = "checkpoints"
DEFAULT_KEEP = 5

GEN_PREFIX = "generation-"
GEN_SUFFIX = ".json"
TMP_SUFFIX = ".tmp"
LOCK_NAME = ".ckptgen.lock"


class CheckpointError(RuntimeError):
    """任务没有任何可读代际时抛出。"""


class _RWLock:
    """简易读写锁：多读单写，写者等待全部读者退出。"""

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    def acquire_read(self):
        with self._cond:
            while self._writer:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        with self._cond:
            while self._writer or self._readers:
                self._cond.wait()
            self._writer = True

    def release_write(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_FALLBACKS = []
_FALLBACKS_GUARD = threading.Lock()


def _get_lock(task_dir):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(task_dir)
        if lock is None:
            lock = _RWLock()
            _LOCKS[task_dir] = lock
        return lock


@contextlib.contextmanager
def _task_lock(task_dir, write):
    """进程内读写锁 + 进程间文件锁，同一任务的写串行化。"""
    os.makedirs(task_dir, exist_ok=True)
    rw_lock = _get_lock(task_dir)
    if write:
        rw_lock.acquire_write()
    else:
        rw_lock.acquire_read()
    lock_file = open(os.path.join(task_dir, LOCK_NAME), "a+")
    try:
        if fcntl is not None:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX if write else fcntl.LOCK_SH,
            )
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        if write:
            rw_lock.release_write()
        else:
            rw_lock.release_read()


def _task_dir(task, root):
    return os.path.join(root or ROOT, task)


def _gen_path(task, generation, root):
    name = GEN_PREFIX + str(generation) + GEN_SUFFIX
    return os.path.join(_task_dir(task, root), name)


def _checksum(payload):
    return hashlib.sha256(payload).hexdigest()


def _list_generations(task_dir):
    if not os.path.isdir(task_dir):
        return []
    generations = []
    for name in os.listdir(task_dir):
        if name.startswith(GEN_PREFIX) and name.endswith(GEN_SUFFIX):
            number = name[len(GEN_PREFIX):-len(GEN_SUFFIX)]
            if number.isdigit():
                generations.append(int(number))
    return sorted(generations)


def _cleanup_stale_temp(task_dir):
    if not os.path.isdir(task_dir):
        return
    for name in os.listdir(task_dir):
        if name.startswith(GEN_PREFIX) and name.endswith(TMP_SUFFIX):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(task_dir, name))


def _fsync_dir(path):
    if not os.path.isdir(path):
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _commit(tmp_path, final_path):
    """原子改名，独立成函数便于测试模拟写到一半崩溃。"""
    os.replace(tmp_path, final_path)
    _fsync_dir(os.path.dirname(final_path))


def save(task, state, root=None, keep=DEFAULT_KEEP):
    """保存一个新代际，返回代际号、保留列表与文件路径。"""
    if not isinstance(keep, int) or keep < 1:
        raise ValueError("keep 必须是不小于 1 的整数")

    task_dir = _task_dir(task, root)
    with _task_lock(task_dir, write=True):
        _cleanup_stale_temp(task_dir)
        generations = _list_generations(task_dir)
        generation = (generations[-1] + 1) if generations else 0
        final_path = _gen_path(task, generation, root)

        payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
        envelope = {
            "generation": generation,
            "checksum": _checksum(payload),
            "state": state,
        }
        tmp_path = final_path + TMP_SUFFIX
        committed = False
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            _commit(tmp_path, final_path)
            committed = True
        finally:
            if not committed and os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)

        removed = _enforce_retention(task_dir, task, root, keep, generation)
        retained = _list_generations(task_dir)
        return {
            "generation": generation,
            "retained": retained,
            "removed": removed,
            "path": final_path,
        }


def _enforce_retention(task_dir, task, root, keep, current_generation):
    """超出保留数量时按最旧优先清理，绝不删除当前代际。"""
    generations = _list_generations(task_dir)
    removable = [g for g in generations if g != current_generation]
    stale = removable[: max(0, len(generations) - keep)]
    for generation in stale:
        with contextlib.suppress(OSError):
            os.remove(_gen_path(task, generation, root))
    return stale


def load(task, generation=None, root=None):
    """读取指定代际；损坏或缺省时逐代回退到上一代。"""
    task_dir = _task_dir(task, root)
    with _task_lock(task_dir, write=False):
        available = _list_generations(task_dir)
        if not available:
            raise CheckpointError("任务 %r 没有任何检查点代际" % task)
        target = available[-1] if generation is None else generation

        candidate = target
        while True:
            path = _gen_path(task, candidate, root)
            try:
                state = _read_checked(path, candidate)
                if candidate != target:
                    _record_fallback(task, target, candidate, "fallback")
                return state
            except (OSError, ValueError, KeyError) as error:
                _record_fallback(task, target, candidate, type(error).__name__)
                lower = [g for g in available if g < candidate]
                if not lower:
                    raise CheckpointError(
                        "任务 %r 的代际 %s 及更早代际均不可读"
                        % (task, target)
                    )
                candidate = lower[-1]


def _read_checked(path, expected_generation):
    with open(path, encoding="utf-8") as handle:
        envelope = json.load(handle)
    if not isinstance(envelope, dict):
        raise ValueError("信封格式损坏")
    if envelope.get("generation") != expected_generation:
        raise ValueError("代际号不匹配")
    if "state" not in envelope or "checksum" not in envelope:
        raise KeyError("信封缺少 state 或 checksum")
    payload = json.dumps(
        envelope["state"], ensure_ascii=False
    ).encode("utf-8")
    actual = _checksum(payload)
    if not hmac.compare_digest(actual, str(envelope["checksum"])):
        raise ValueError("校验和不匹配")
    return envelope["state"]


def _record_fallback(task, requested, used, reason):
    record = {
        "task": task,
        "requested": requested,
        "used": used,
        "reason": reason,
    }
    with _FALLBACKS_GUARD:
        _FALLBACKS.append(record)
    return record


def fallback_records(task=None):
    """返回损坏/缺省回退记录，可按任务过滤。"""
    with _FALLBACKS_GUARD:
        records = list(_FALLBACKS)
    if task is not None:
        records = [record for record in records if record["task"] == task]
    return records


def retained_generations(task, root=None):
    """返回当前保留的代际号列表（旧到新）。"""
    task_dir = _task_dir(task, root)
    with _task_lock(task_dir, write=False):
        return _list_generations(task_dir)


def current_generation(task, root=None):
    """返回最新代际号，没有任何代际时返回 None。"""
    generations = retained_generations(task, root)
    return generations[-1] if generations else None
