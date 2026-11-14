"""回填计划，当前整段一个分片。"""


def plan(range_spec, shards=1):
    """无论请求多少分片都只生成一个。"""
    return {"range": dict(range_spec), "shards": [dict(range_spec)]}


def run(plan_payload, workers=1):
    """顺序跑，workers被忽略。"""
    return {"shards": len(plan_payload["shards"]), "workers": 1, "done": len(plan_payload["shards"])}
