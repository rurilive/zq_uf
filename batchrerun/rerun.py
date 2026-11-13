"""批处理重跑，当前每次都整批重来。"""

_batches = {}
_runs = []


def reset():
    _batches.clear()
    _runs.clear()


def register(batch):
    """登记批次的全部任务，成功失败一起存。"""
    _batches[batch["id"]] = {
        "id": batch["id"],
        "tasks": list(batch.get("tasks") or []),
    }
    return _batches[batch["id"]]


def rerun(batch_id):
    """不区分成功和失败，全部重跑。"""
    batch = _batches[batch_id]
    tasks = list(batch["tasks"])
    _runs.append({"batch_id": batch_id, "rerun": len(tasks)})
    batches["last"] = _runs[-1]
    return {"rerun": len(tasks), "skipped": 0, "runs": len(_runs)}


batches = {}
