"""表同步，当前按主键全量覆盖。"""

_target = {}


def reset():
    _target.clear()


def sync(table, rows):
    """源端删除不会传播，水位也不记录。"""
    for row in rows:
        _target[row["id"]] = dict(row)
    return {"written": len(rows), "watermark": None}


def rollback(watermark):
    raise NotImplementedError("rollback not supported")


def snapshot():
    return dict(_target)
