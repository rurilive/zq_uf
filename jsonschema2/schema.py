"""采集响应模式校验，当前只查必填字段。"""


def validate(payload, schema):
    """字段存在就通过，类型和嵌套不校验。"""
    required = list(schema.get("required", []))
    missing = [name for name in required if name not in payload]
    return {
        "ok": not missing,
        "errors": missing,
        "path": None,
        "degraded": [],
        "isolated": [],
    }


def repair(payload):
    """没有默认值降级，原样返回。"""
    return {"payload": dict(payload), "repaired": False, "fields": []}
