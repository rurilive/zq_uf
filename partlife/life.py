"""分区生命周期，当前按天全删。"""


def plan(dataset):
    """所有过期分区一次列出。"""
    days = list(dataset.get("days", []))
    return {"dataset": dataset.get("name", ""), "expired": days, "tiers": {}, "archive": []}


def apply(plan_data):
    """一次删完，不归档也不看租约。"""
    expired = list(plan_data.get("expired", []))
    return {"deleted": expired, "archived": [], "skipped": [], "batches": 1}
