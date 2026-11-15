"""采集结果核对，当前只查字段是否存在。"""


def verify(rows, schema):
    """字段齐全就通过，不校验类型和取值。"""
    required = list(schema.get("required", []))
    passed, isolated = [], []
    for index, row in enumerate(rows):
        missing = [name for name in required if name not in row]
        if missing:
            isolated.append({"index": index, "reason": "missing", "fields": missing})
        else:
            passed.append(index)
    return {"passed": passed, "isolated": isolated, "rolled_back": [], "sampled": []}


def rollback(batch):
    """回退整批，不做行级区分。"""
    return {"batch": batch, "rolled_back": "all"}
