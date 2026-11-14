"""会话落盘，当前明文JSON。"""
import json
import os

DEFAULT_DIR = "sessions"


def _path(account, root=None):
    return os.path.join(root or DEFAULT_DIR, account + ".json")


def save(account, session, root=None):
    """明文写盘，没有密钥版本。"""
    path = _path(account, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(session, handle, ensure_ascii=False)
    return path


def load(account, root=None):
    with open(_path(account, root), encoding="utf-8") as handle:
        return json.load(handle)
