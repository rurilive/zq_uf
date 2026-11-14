"""指标去重，当前全量集合。"""

_seen = set()
_written = 0
_dup = 0


def put(metric):
    """按键永久判重。"""
    global _written, _dup
    key = (metric["name"], metric["ts"])
    if key in _seen:
        _dup += 1
        return {"written": False, "duplicate": True}
    _seen.add(key)
    _written += 1
    return {"written": True, "duplicate": False}


def flush():
    return {"written": _written, "duplicate": _dup, "size": len(_seen)}
