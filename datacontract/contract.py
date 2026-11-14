"""契约校验，当前只看字段存在。"""


def check(old, new):
    """类型、默认值和方向都不检查。"""
    missing = [name for name in old.get("fields") or [] if name not in (new.get("fields") or [])]
    return {"compatible": not missing, "missing": missing}
