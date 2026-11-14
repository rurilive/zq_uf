"""幂等写入，当前直接按主键覆盖。"""

_rows = {}


def reset():
    _rows.clear()


def write(rows, idem_key=None):
    """不看版本和幂等键。"""
    for row in rows:
        _rows[row["key"]] = dict(row)
    return {"written": len(rows), "rows": dict(_rows)}
