"""行版本写入，当前直接覆盖。"""

_rows = {}


def reset():
    _rows.clear()


def update(key, row, version=1):
    """版本只是入参，不参与校验。"""
    _rows[key] = dict(row)
    return {"ok": True, "key": key}


def get(key):
    return _rows.get(key)
