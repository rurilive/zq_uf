"""表复制，当前全量。"""

_source = {}
_target = {}


def copy(src, dst):
    """一次性拷完，不处理并发写。"""
    _target.clear()
    _target.update({row["id"]: dict(row) for row in src})
    return {"rows": len(_target)}


def verify():
    """只比行数。"""
    return {"ok": len(_source) == len(_target)}
