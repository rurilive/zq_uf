"""入库前的幂等写入层：版本化条件写入、分批提交与并发控制。

纯 Python 标准库实现，存储载体仍是普通 ``dict``，业务键到完整行的
映射关系不变；版本信息与每把业务键的锁维护在模块级的按存储注册表中，
因此 :func:`upsert` 的返回值依旧是普通 ``dict``，老调用方无感知。
"""

import threading

__all__ = [
    "upsert",
    "get_stats",
    "reset_stats",
    "drop_store",
    "BatchWriteError",
    "WriteConflict",
    "VERSION_FIELD",
    "DEFAULT_BATCH_SIZE",
    "MAX_COMMIT_ATTEMPTS",
]

VERSION_FIELD = "version"
DEFAULT_BATCH_SIZE = 100
MAX_COMMIT_ATTEMPTS = 10

_ABSENT = object()
_NO_VERSION = object()


class BatchWriteError(RuntimeError):
    """某个批次提交失败；前面已提交的批次保持有效，可整批重试。"""

    def __init__(self, message, batch_index=None, cause=None, stats=None):
        super().__init__(message)
        self.batch_index = batch_index
        self.cause = cause
        self.stats = stats


class WriteConflict(RuntimeError):
    """条件更新在规定重试次数内一直与并发写冲突。"""

    def __init__(self, message, key=None, attempts=None):
        super().__init__(message)
        self.key = key
        self.attempts = attempts


class _StoreState:
    """每个存储实例一份的锁、版本表和累计统计。"""

    def __init__(self):
        self.global_lock = threading.RLock()
        self.key_locks = {}
        # 业务键是 tuple，无法弱引用，版本表按存储实例独立维护。
        self.versions = {}
        self.stats = {
            "inserted": 0,
            "updated": 0,
            "duplicates": 0,
            "stale": 0,
            "conflict_retries": 0,
            "batches_committed": 0,
            "batches_failed": 0,
        }

    def lock_for(self, key):
        with self.global_lock:
            lock = self.key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self.key_locks[key] = lock
            return lock


# 普通 dict 不支持弱引用，按 id 注册；store 弃用后可调用 drop_store 清理。
_STATES = {}
_STATE_GUARD = threading.Lock()


def _state_of(store):
    identity = id(store)
    state = _STATES.get(identity)
    if state is None:
        with _STATE_GUARD:
            state = _STATES.get(identity)
            if state is None:
                state = _StoreState()
                _STATES[identity] = state
    return state


def drop_store(store):
    """删除存储实例对应的版本、锁与统计，返回是否存在过。"""
    with _STATE_GUARD:
        return _STATES.pop(id(store), None) is not None


def get_stats(store):
    """返回该存储实例的累计写入统计（副本，可安全读取）。"""
    return dict(_state_of(store).stats)


def reset_stats(store):
    """清零累计统计，返回清零前的统计快照。"""
    state = _state_of(store)
    with state.global_lock:
        snapshot = dict(state.stats)
        for name in state.stats:
            state.stats[name] = 0
    return snapshot


def _existing_version(state, store, key):
    """读取业务键当前版本；未写入返回 _ABSENT，无版本返回 _NO_VERSION。"""
    if key not in store:
        return _ABSENT
    return state.versions.get(key, _NO_VERSION)


def _plan_row(state, store, row, keys):
    """按业务键查现有版本，决定本行的写入动作。"""
    key = tuple(row.get(name) for name in keys)
    incoming = row.get(VERSION_FIELD)
    current = _existing_version(state, store, key)

    if current is _ABSENT:
        return key, "insert", incoming

    if incoming is None or current is _NO_VERSION:
        # 无版本数据走最后写入生效，保持对老数据的兼容。
        return key, "replace", incoming

    if incoming > current:
        return key, "update", incoming

    # 同版本（重复插入/重试）与旧版本（回退）都不覆盖。
    action = "duplicate" if incoming == current else "stale"
    return key, action, incoming


def _chunks(items, batch_size):
    for start in range(0, len(items), batch_size):
        yield start // batch_size, items[start:start + batch_size]


def _commit_batch(state, store, entries, batch_index, on_batch):
    """提交单个批次，冲突时重读再判并重试。

    按业务键排序后统一加锁，避免多线程交叉加锁导致死锁；批次内任意一行
    条件更新失败都会回滚整批并重做，保证批次原子性。
    """
    ordered = sorted(entries, key=lambda entry: repr(entry[2]))
    acquired = []
    try:
        for _row, _keys, key in ordered:
            state.lock_for(key).acquire()
            acquired.append(key)

        # 加锁后先按业务键查一次现有版本，作为条件更新的预期。
        planned = [
            _plan_row(state, store, row, row_keys)
            for row, row_keys, _key in entries
        ]

        attempt = 0
        while True:
            attempt += 1
            try:
                if on_batch is not None:
                    # 测试/故障注入点：抛异常模拟批次提交失败（整批可
                    # 重试）；也可直接改动 store/版本表，模拟判定之后、
                    # 提交之前发生的并发写入。
                    on_batch(batch_index)
            except Exception as exc:
                state.stats["batches_failed"] += 1
                raise BatchWriteError(
                    "batch %d failed before commit" % batch_index,
                    batch_index=batch_index,
                    cause=exc,
                    stats=get_stats(store),
                )

            # 条件更新：重读现有版本，与本轮预期不一致即视为并发冲突，
            # 整批重读再判并重试。
            rechecked = [
                _plan_row(state, store, row, row_keys)
                for row, row_keys, _key in entries
            ]
            if rechecked != planned:
                state.stats["conflict_retries"] += 1
                if attempt == MAX_COMMIT_ATTEMPTS:
                    raise WriteConflict(
                        "batch %d conflicted %d times" % (
                            batch_index, MAX_COMMIT_ATTEMPTS),
                        attempts=MAX_COMMIT_ATTEMPTS,
                    )
                planned = rechecked
                continue

            snapshot = [
                (key, store.get(key), state.versions.get(key))
                for key, _action, _version in rechecked
            ]
            try:
                for index, (key, action, incoming) in enumerate(rechecked):
                    if action in ("insert", "update", "replace"):
                        # 唯一键冲突不会抛出：键已存在时动作在重读阶段被
                        # 改写成 update/duplicate/stale，再走版本比较。
                        store[key] = dict(entries[index][0])
                        if incoming is None:
                            # 无版本行不登记版本，后续仍按无版本规则处理。
                            state.versions.pop(key, None)
                        else:
                            state.versions[key] = incoming
                    # duplicate / stale 不写入，统计在提交成功后累加。
            except Exception as exc:  # 应用阶段异常：回滚整批后转成批次失败
                for key, old_row, old_version in snapshot:
                    if old_row is None:
                        store.pop(key, None)
                        state.versions.pop(key, None)
                    else:
                        store[key] = old_row
                        if old_version is not None:
                            state.versions[key] = old_version
                        else:
                            state.versions.pop(key, None)
                state.stats["batches_failed"] += 1
                raise BatchWriteError(
                    "batch %d failed while applying writes" % batch_index,
                    batch_index=batch_index,
                    cause=exc,
                    stats=get_stats(store),
                )

            for _key, action, _version in rechecked:
                if action == "insert":
                    state.stats["inserted"] += 1
                elif action in ("update", "replace"):
                    state.stats["updated"] += 1
                elif action == "duplicate":
                    state.stats["duplicates"] += 1
                else:
                    state.stats["stale"] += 1
            state.stats["batches_committed"] += 1
            return rechecked

    finally:
        for key in reversed(acquired):
            state.lock_for(key).release()


def upsert(rows, keys, store=None, batch_size=DEFAULT_BATCH_SIZE, on_batch=None):
    """按业务键幂等、带版本地写入。

    rows: 待写入行的可迭代对象，行内用 ``version`` 字段表示版本；
    keys: 业务键字段名列表，支持复合键；
    store: 存储用 dict，不传则新建；同一 store 重跑即可安全重试；
    batch_size: 每批行数，批次顺序提交，失败不影响已提交批次；
    on_batch: 每批每次提交尝试前的回调，参数为批次序号，可用于故障注入。

    版本规则：新版本覆盖，同版本计为重复跳过，旧版本计为回退忽略；
    无版本的行与无版本的存量数据按最后写入生效处理，兼容旧调用方。
    """
    if store is None:
        store = {}
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if not keys:
        raise ValueError("keys must not be empty")

    state = _state_of(store)
    entries = []
    for row in rows:
        entries.append((row, keys, tuple(row.get(name) for name in keys)))

    for batch_index, batch in _chunks(entries, batch_size):
        try:
            _commit_batch(state, store, batch, batch_index, on_batch)
        except BatchWriteError:
            raise
        except WriteConflict as exc:
            state.stats["batches_failed"] += 1
            raise BatchWriteError(
                "batch %d failed due to write conflict" % batch_index,
                batch_index=batch_index,
                cause=exc,
                stats=get_stats(store),
            )
    return store
