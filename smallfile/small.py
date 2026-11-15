"""小文件合并，当前全部重写。"""


def plan(dataset):
    """所有分区都参与合并。"""
    parts = list(dataset.get("partitions", []))
    return {"partitions": parts, "selected": parts, "files": len(parts)}


def compact(plan_data):
    """一次重写全部分区。"""
    parts = list(plan_data.get("partitions", []))
    return {"compacted": parts, "skipped": [], "switched": True, "failed": []}
