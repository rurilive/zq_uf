"""批处理重跑：按失败分区补跑，批次加锁，历史可序列化。"""

import threading

_batches = {}
_locks = {}
_locks_guard = threading.Lock()

batches = {}
history = []


def reset():
    _batches.clear()
    batches.clear()
    history.clear()
    with _locks_guard:
        _locks.clear()


def register(batch):
    """登记批次任务；分区未知，首次重跑整批执行。"""
    if not isinstance(batch, dict) or not batch.get("id"):
        raise ValueError("batch must be a dict with a non-empty 'id'")
    tasks = batch.get("tasks")
    if tasks is None:
        tasks = []
    if not isinstance(tasks, list):
        raise ValueError("batch 'tasks' must be a list")
    _batches[batch["id"]] = {
        "id": batch["id"],
        "tasks": list(tasks),
        "partitions": None,
        "runs": 0,
    }
    return _batches[batch["id"]]


def rerun(batch_id):
    """只补跑失败分区；同批次进行中时返回 {"status": "running"}。"""
    batch = _batches.get(batch_id)
    if batch is None:
        raise KeyError(f"unknown batch: {batch_id!r}")
    lock = _lock_for(batch_id)
    if not lock.acquire(blocking=False):
        return {"batch_id": batch_id, "status": "running", "runs": batch["runs"]}
    try:
        return _rerun_locked(batch)
    finally:
        lock.release()


def _lock_for(batch_id):
    with _locks_guard:
        lock = _locks.get(batch_id)
        if lock is None:
            lock = _locks[batch_id] = threading.Lock()
        return lock


def _rerun_locked(batch):
    partitions = batch["partitions"]
    if partitions is None:
        targets = list(batch["tasks"])
        skipped = 0
        succeeded = []
    else:
        failed_names = set(partitions["failed"])
        targets = [t for t in batch["tasks"] if t.get("name") in failed_names]
        skipped = len(partitions["succeeded"])
        succeeded = list(partitions["succeeded"])
    failed = []
    for task in targets:
        if _execute(task):
            succeeded.append(task.get("name"))
        else:
            failed.append(task.get("name"))
    batch["partitions"] = {"succeeded": succeeded, "failed": failed}
    batch["runs"] += 1
    record = {
        "batch_id": batch["id"],
        "rerun": batch["runs"],
        "tasks": len(targets),
        "skipped": skipped,
        "succeeded": list(succeeded),
        "failed": list(failed),
    }
    history.append(record)
    batches["last"] = record
    batches[batch["id"]] = batch["partitions"]
    return {"rerun": len(targets), "skipped": skipped, "runs": batch["runs"]}


def _execute(task):
    """执行单个任务，返回是否成功；测试可替换以注入延迟或失败。"""
    return bool(task.get("ok"))
