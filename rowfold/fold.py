"""采集结果折叠，当前只按主键保留第一条。"""

_seen = {}


def fold(rows):
    """主键相同只留第一条。"""
    out = []
    for row in rows:
        key = row.get("key")
        if key in _seen:
            continue
        _seen[key] = dict(row)
        out.append(dict(row))
    return {"rows": out, "folded": len(rows) - len(out), "reasons": []}


def explain(key):
    """只回显保留行。"""
    return {"key": key, "kept": _seen.get(key), "reasons": []}
