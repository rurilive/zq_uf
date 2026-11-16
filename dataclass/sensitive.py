"""敏感列发现，当前只看列名。"""

_RULES = {
    "phone": "phone",
    "mobile": "phone",
    "email": "email",
    "idcard": "id",
    "id_card": "id",
    "address": "address",
}

_RESULTS = {}


def discover(table):
    """列名精确命中规则表才算发现，内容一律不看。"""
    if not isinstance(table, dict):
        raise ValueError("table must be a dict")
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise ValueError("columns must be a list")
    name = table.get("name", "")
    found = []
    for index, column in enumerate(columns):
        column_name = str(column.get("name", ""))
        kind = _RULES.get(column_name.lower(), "")
        if not kind:
            continue
        found.append({
            "column": column_name,
            "index": index,
            "kind": kind,
            "level": "",
            "samples": [],
            "evidence": "name",
        })
    _RESULTS[name] = found
    return {
        "table": name,
        "sensitive": len(found),
        "columns": found,
    }


def hits(table_name):
    """按表名取命中列，没有就返回空列表。"""
    return [dict(item) for item in _RESULTS.get(table_name, [])]


def report():
    """只报总数，等级和样例都是空的。"""
    total = 0
    for found in _RESULTS.values():
        total += len(found)
    return {
        "tables": len(_RESULTS),
        "sensitive": total,
        "levels": {},
        "samples": [],
    }
