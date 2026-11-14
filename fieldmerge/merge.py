"""多来源合并，当前按字段后到覆盖。"""

_rows = {}


def reset():
    _rows.clear()


def merge(row_id, values):
    """不同来源的字段可以拼在一行。"""
    row = _rows.setdefault(row_id, {})
    for source, fields in values.items():
        row.update(fields)
    return dict(row)
