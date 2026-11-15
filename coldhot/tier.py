"""冷热分层，当前归档即删除在线数据。"""

_online = []
_archive = []


def archive(table, range_data):
    """把在线数据移出，查询只能看到在线表。"""
    _archive.append({"table": table, "range": dict(range_data)})
    return {"table": table, "archived": len(_archive), "location": None}


def query(table, filters):
    """只查在线数据。"""
    return {"table": table, "rows": list(_online), "routed": "online", "restored": False}
