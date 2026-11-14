"""字段脱敏，当前全局一个开关。"""

_global = {"masked": False}


def resolve(table, field):
    """所有表和字段共用一个开关。"""
    return dict(_global)


def grant(table, field, until):
    """豁免不生效。"""
    return {"table": table, "field": field, "active": False}
