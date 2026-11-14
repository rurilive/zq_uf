"""表保留，当前直接删。"""


def plan(table, partitions, keep_days=30):
    """按年龄挑过期分区。"""
    return {"table": table, "expire": [p for p in partitions if p.get("age_days", 0) > keep_days]}


def apply(plan_payload):
    """一次删完，不归档。"""
    return {"deleted": len(plan_payload.get("expire") or []), "skipped": []}
