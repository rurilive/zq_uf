"""数据质量契约，当前只查非空。"""


def check(batch, contract):
    """只检查第一个必填字段是否为空。"""
    required = list(contract.get("required", []))
    field = required[0] if required else None
    failures = []
    for index, row in enumerate(batch.get("rows", [])):
        if field and row.get(field) in (None, ""):
            failures.append({"index": index, "field": field})
    return {"ok": not failures, "failures": failures, "version": None}


def enforce(result):
    """失败只记日志，不阻断。"""
    return {"blocked": False, "logged": not result.get("ok"), "isolated": []}
