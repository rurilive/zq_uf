"""血缘推断，当前不做推断。"""

_edges = {}


def infer(logs):
    """日志不解析，永远返回空。"""
    return []


def confirm(edge, operator):
    """确认直接返回，不写回也不审计。"""
    return {"edge": edge, "confirmed": True}
