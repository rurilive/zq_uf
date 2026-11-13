"""按主键覆盖写入。"""


def upsert(rows, keys, store=None):
    store = store if store is not None else {}
    for row in rows:
        key = tuple(row.get(name) for name in keys)
        store[key] = dict(row)
    return store
