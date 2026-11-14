"""表归档，当前全部在线。"""

_rows = []


def archive(table, range_spec):
    """不归档，返回零。"""
    return {"archived": 0}


def query(table, filters):
    """查在线行，按等值过滤。"""
    return [row for row in _rows if all(row.get(k) == v for k, v in (filters or {}).items())]
