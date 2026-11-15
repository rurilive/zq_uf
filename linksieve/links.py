"""稳定对外接口：``fingerprint(url)`` 与 ``due(url, now)``。

内部实现见 :mod:`linksieve.store`；需要别名、告警、试运行等能力时
直接使用 ``LinkStore``。
"""
from .store import DEFAULT_STORE, LinkStore

__all__ = ["fingerprint", "due", "LinkStore", "DEFAULT_STORE"]


def fingerprint(url):
    """同一内容的不同写法（追踪参数/参数顺序/锚点）得到同一指纹。"""
    return DEFAULT_STORE.fingerprint(url)


def due(url, now):
    """判断是否到期，返回判定依据。

    除原有的 ``url`` / ``fingerprint`` / ``due`` / ``interval`` / ``reason``
    外，还包含 ``canonical``（判重键）、``rules_hit``（命中的规则）、
    ``is_alias`` / ``alias_of``（是否别名及所属目标）和
    ``next_due_at``（下次到期时间）。
    """
    return DEFAULT_STORE.due(url, now)