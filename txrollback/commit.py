"""批次写入：提交时记录补偿日志，支持按批反向补偿回滚。"""

STORE = {}
JOURNAL = {}
PENDING = []

DEFAULT_MAX_ROWS_PER_BATCH = 1000

_STATS = {"committed": 0, "rolled_back": 0, "pending": 0}
_MAX_ROWS_PER_BATCH = DEFAULT_MAX_ROWS_PER_BATCH

_OPS = ("insert", "update", "delete")


class BatchError(ValueError):
    """批次参数或阈值校验失败。"""


class CompensationError(RuntimeError):
    """单行补偿校验失败，转入待人工队列。"""


def configure(max_rows_per_batch=None):
    """调整阈值；reset() 会恢复默认值。"""
    global _MAX_ROWS_PER_BATCH
    if max_rows_per_batch is not None:
        if not isinstance(max_rows_per_batch, int) or max_rows_per_batch < 1:
            raise BatchError("max_rows_per_batch 必须是正整数")
        _MAX_ROWS_PER_BATCH = max_rows_per_batch


def reset():
    global _MAX_ROWS_PER_BATCH
    STORE.clear()
    JOURNAL.clear()
    PENDING.clear()
    _STATS.update(committed=0, rolled_back=0, pending=0)
    _MAX_ROWS_PER_BATCH = DEFAULT_MAX_ROWS_PER_BATCH


def stats():
    """提交、回滚、待处理（待人工）行数。"""
    return dict(_STATS)


def _validate(batch):
    if not isinstance(batch, dict):
        raise BatchError("batch 必须是 dict")
    batch_id = batch.get("id")
    if not batch_id:
        raise BatchError("batch 缺少 id")
    rows = batch.get("rows")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise BatchError("batch rows 必须是 list")
    if len(rows) > _MAX_ROWS_PER_BATCH:
        raise BatchError(
            f"batch 行数 {len(rows)} 超过阈值 {_MAX_ROWS_PER_BATCH}"
        )
    for row in rows:
        if not isinstance(row, dict) or "key" not in row:
            raise BatchError("每行必须是含 key 的 dict")
        if row.get("op", "insert") not in _OPS:
            raise BatchError(f"未知操作类型 {row.get('op')}")
    return batch_id, rows


def _apply(row):
    key = row["key"]
    op = row.get("op", "insert")
    before = STORE.get(key)
    if op == "delete":
        if key not in STORE:
            raise BatchError(f"delete 失败：key {key} 不存在")
        del STORE[key]
        after = None
    else:
        if op == "update" and key not in STORE:
            raise BatchError(f"update 失败：key {key} 不存在")
        STORE[key] = row
        after = row
    return {"key": key, "op": op, "before": before, "after": after}


def commit(batch):
    """提交一批写入，逐行记录主键、操作类型和前后镜像到补偿日志。

    中途失败抛错，已写行保留在库里，日志状态置为 failed，
    可用 rollback(batch_id) 反向补偿。同一 batch_id 已成功提交过时
    直接返回首次结果，不重复写入（幂等）。
    """
    batch_id, rows = _validate(batch)
    journal = JOURNAL.get(batch_id)
    if journal and journal["status"] == "committed":
        return {
            "batch_id": batch_id,
            "written": [entry["key"] for entry in journal["ops"]],
            "idempotent": True,
        }
    journal = {"status": "committed", "ops": []}
    JOURNAL[batch_id] = journal
    written = []
    for row in rows:
        if row.get("fail"):
            journal["status"] = "failed"
            raise RuntimeError(f"row {row.get('key')} failed")
        try:
            entry = _apply(row)
        except BatchError:
            journal["status"] = "failed"
            raise
        journal["ops"].append(entry)
        written.append(row["key"])
        _STATS["committed"] += 1
    return {"batch_id": batch_id, "written": written}


def _compensate(entry):
    key = entry["key"]
    current = STORE.get(key)
    if current != entry["after"]:
        raise CompensationError(
            f"key {key} 当前值与提交时不一致，可能已被外部修改"
        )
    if entry["before"] is None:
        del STORE[key]
    else:
        STORE[key] = entry["before"]


def rollback(batch_id):
    """按补偿日志反向恢复 before 镜像。

    每行补偿前校验当前值与提交时一致，不一致则转入待人工队列
    （PENDING）并继续处理其余行。重复回滚同一批次直接返回空结果，
    不重复补偿（幂等）。
    """
    journal = JOURNAL.get(batch_id)
    if journal is None:
        raise NotImplementedError(f"unknown batch_id: {batch_id}")
    if journal["status"] == "rolled_back":
        return {
            "batch_id": batch_id,
            "compensated": [],
            "pending": [],
            "idempotent": True,
        }
    compensated = []
    pending = []
    for entry in reversed(journal["ops"]):
        try:
            _compensate(entry)
        except CompensationError as exc:
            item = {
                "batch_id": batch_id,
                "key": entry["key"],
                "op": entry["op"],
                "reason": str(exc),
            }
            PENDING.append(item)
            pending.append(item)
            _STATS["pending"] += 1
        else:
            compensated.append(entry["key"])
            _STATS["rolled_back"] += 1
    journal["status"] = "rolled_back"
    return {"batch_id": batch_id, "compensated": compensated, "pending": pending}
