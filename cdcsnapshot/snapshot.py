"""快照与变更，当前各自更新。"""

_rows = {}


def snapshot(table):
    """全量导出当前行。"""
    return {"table": table, "rows": len(_rows), "data": dict(_rows)}


def apply(events):
    """按到达顺序追加，没有位点。"""
    for event in events:
        _rows[event["key"]] = event["row"]
    return {"applied": len(events)}
