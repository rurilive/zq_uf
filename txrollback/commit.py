"""批次写入，当前逐行提交且没有补偿。"""

STORE = {}


def reset():
    STORE.clear()


def commit(batch):
    """逐行写入，中途失败已写的行留在库里。"""
    batch_id = batch.get("id")
    written = []
    for row in batch.get("rows") or []:
        if row.get("fail"):
            raise RuntimeError(f"row {row.get('key')} failed")
        STORE[row["key"]] = row
        written.append(row["key"])
    return {"batch_id": batch_id, "written": written}


def rollback(batch_id):
    """没有补偿日志，无法回滚。"""
    raise NotImplementedError("rollback not supported")
