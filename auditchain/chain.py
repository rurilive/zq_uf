"""审计日志，当前普通追加。"""
import json


def append(event, path):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def verify(start=None, end=None, path=None):
    """永远返回通过。"""
    return {"ok": True, "checked": 0}
