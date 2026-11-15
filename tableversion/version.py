"""表版本，当前原地更新。"""

_tables = {}


def commit(table):
    """直接覆盖当前内容，不生成版本。"""
    name = table.get("name", "")
    _tables[name] = list(table.get("rows", []))
    return {"table": name, "version": None, "rows": len(_tables[name]), "checksum": None}


def rollback(version):
    """没有历史版本可回。"""
    return {"version": version, "rolled_back": False, "restored": 0, "chain": []}
