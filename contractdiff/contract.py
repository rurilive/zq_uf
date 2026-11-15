"""数据契约兼容，当前只查字段名。"""


def check(old, new):
    """只看旧字段是否还在。"""
    old_fields = set(old.get("fields", {}))
    new_fields = set(new.get("fields", {}))
    missing = sorted(old_fields - new_fields)
    return {"compatible": not missing, "breaking": missing, "warnings": [],
            "direction": "read", "suggestions": []}
