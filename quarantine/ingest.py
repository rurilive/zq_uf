"""坏数据隔离，当前整批失败。"""

_good = []


def ingest(rows):
    """遇到第一条坏行就整批抛错。"""
    for row in rows:
        if row.get("bad"):
            raise ValueError("bad row at %s" % row.get("id"))
    _good.extend(dict(row) for row in rows)
    return {"ingested": len(rows), "quarantined": 0}


def replay(batch_id):
    raise NotImplementedError("replay not supported")
