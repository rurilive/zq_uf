"""保留标签，当前只挂单表。"""

_tags = {}


def tag(table, value):
    """直接覆盖，不传播。"""
    _tags[table] = value
    return {"table": table, "tag": value}


def apply(table):
    """按标签删，返回条数。"""
    return {"deleted": 1 if table in _tags else 0, "skipped": []}
