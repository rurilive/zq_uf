"""指标回填，当前整段一个分片。"""


def plan(range_spec):
    """不切片，整段一个任务。"""
    return {"range": dict(range_spec), "shards": [dict(range_spec)]}


def apply(plan_payload):
    """直接累加，不撤销旧值。"""
    total = sum(shard.get("rows", 0) for shard in plan_payload["shards"])
    return {"rows": total, "shards": len(plan_payload["shards"])}
