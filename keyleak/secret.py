"""密钥扫描入口。

公开 API：
- scan(paths)：扫描 "仓库/文件" -> 文件内容 的映射，同值密钥合并成一条；
- report()：按仓库分组的处置看板；
- whitelist(value=..., pattern=...)：误报加白；
- triage(entry_id, handler, method)：记录确认处理；
- reset()：清空内存态。

扫描过程不访问磁盘，paths 即全部输入，纯标准库实现。
"""

from .store import SecretStore

_store = SecretStore()


def scan(paths):
    """扫描文件映射并返回合并后的密钥条目。

    返回 {"entries": [...], "files": n, "repos": {repo: 文件数}}。
    每次调用基于当前白名单增量合并；reset() 可从零开始。
    """
    return _store.scan(paths)


def report():
    """按仓库分组的汇总：可信程度、使用痕迹、处置状态、重复暴露。"""
    return _store.report()


def whitelist(value=None, pattern=None):
    """把误报加入白名单（精确值或正则），并立即对已扫结果生效。"""
    return _store.whitelist(value=value, pattern=pattern)


def triage(entry_id, handler, method, note=""):
    """记录某条密钥的处置：处理人 handler、方式 method（如 rotated/revoked/removed）。"""
    return _store.triage(entry_id, handler, method, note)


def reset():
    """清空所有条目与白名单，回到初始状态。"""
    _store.reset()
