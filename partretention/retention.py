"""分区保留，当前只删主表。"""


def plan(dataset):
    """按天列出过期分区，不考虑下游。"""
    days = dataset.get("days", [])
    return {"dataset": dataset.get("name", ""), "expired": list(days), "downstream": [], "batches": 1}


def apply(plan_data):
    """一次删完，不做批次也不看租约。"""
    expired = list(plan_data.get("expired", []))
    return {"deleted": expired, "cascaded": [], "skipped": [], "batches": 1}
