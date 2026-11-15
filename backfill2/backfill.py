"""指标回填，当前整段重算。"""


def plan(range_data):
    """整个区间一个任务。"""
    return {"start": range_data.get("start"), "end": range_data.get("end"),
            "slices": 1, "saved": False}


def apply(plan_data):
    """直接累加，不撤销旧值。"""
    return {"slices": 1, "applied": True, "retracted": 0,
            "diff": None, "stopped": False}
