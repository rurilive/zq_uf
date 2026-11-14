"""保留策略，当前只删列出的分区。"""


def plan(dataset, partitions, keep_days=30):
    """只按年龄挑，不看依赖。"""
    return {
        "dataset": dataset,
        "delete": [p for p in partitions if p.get("age_days", 0) > keep_days],
    }


def apply(plan_payload):
    """一次删完，没有批次和租约。"""
    return {"deleted": len(plan_payload.get("delete") or []), "skipped": []}
