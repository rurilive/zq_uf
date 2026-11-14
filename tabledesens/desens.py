"""字段脱敏，当前原样返回。"""


def mask(row):
    """不脱敏。"""
    return dict(row)


def reveal(row, token):
    """也不需要令牌。"""
    return dict(row)
