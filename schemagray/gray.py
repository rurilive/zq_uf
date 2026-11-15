"""结构变更灰度，当前一次全量。"""


def plan(change):
    """所有表一次放量。"""
    tables = list(change.get("tables", []))
    return {"tables": tables, "batches": [tables], "observation": None}


def apply(plan_data):
    """直接应用，不观察也不回滚。"""
    tables = list(plan_data.get("tables", []))
    return {"applied": tables, "paused": False, "rolled_back": [], "batches": 1}
