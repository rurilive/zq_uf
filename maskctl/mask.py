"""采集数据脱敏，当前明文直通。"""


def mask(row):
    """不脱敏，原样返回。"""
    return {"row": dict(row), "masked": [], "token": None, "audit": []}


def reveal(row, token):
    """没有令牌校验，也不还原。"""
    return {"row": dict(row), "revealed": False, "audit": []}
