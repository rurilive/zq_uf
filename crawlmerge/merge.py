"""分片合并，当前后到覆盖。"""

_rows = {}


def merge(shards):
    """按完成顺序写入，后到的赢。"""
    for shard in shards:
        for row in shard:
            _rows[row["id"]] = dict(row)
    return {"rows": len(_rows), "conflicts": []}


def resume(token=None):
    """没有断点。"""
    return {"done": 0}
