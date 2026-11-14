"""结构版本，当前只保留最新。"""

_current = {"version": 0, "fields": {}}


def register(version):
    """直接覆盖，不检查兼容。"""
    _current.update({"version": version["version"], "fields": version.get("fields") or {}})
    return dict(_current)


def resolve(version=None):
    """version 参数被忽略。"""
    return dict(_current)
