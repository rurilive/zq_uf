"""模块级门面：保持 add(url) / same(a, b) 接口不变，委托全局增量索引。

需要多实例或显式落盘时直接使用 urlnorm2.index.URLNormIndex。
"""

from .index import URLNormIndex
from .rules import Rule, SameResult, make_strip_query_rule

_index = URLNormIndex()


def get_index():
    return _index


def reset_index(rules=None):
    """重置全局索引（主要给测试和进程切换数据源使用）。"""
    global _index
    _index = URLNormIndex(rules=rules)
    return _index


def add(url):
    """增量并入一条链接，返回新增/合并/代表/类规模等信息。"""
    return _index.add(url)


def same(a, b):
    """判定两 URL 是否等价；结果可直接当 bool，用 .rule 看命中的规则。"""
    return _index.same(a, b)


def remove(url):
    return _index.remove(url)


def remove_many(urls):
    return _index.remove_many(urls)


def size():
    return _index.size()


def class_of(url):
    return _index.class_of(url)


def class_info(class_id):
    return _index.class_info(class_id)


def recompute(rule_id=None, progress=None):
    return _index.recompute(rule_id=rule_id, progress=progress)


def update_rule(rule_id, apply, version=None, progress=None):
    return _index.update_rule(rule_id, apply, version=version, progress=progress)


def add_rule(rule, position=None, progress=None):
    return _index.add_rule(rule, position=position, progress=progress)


def save(path):
    return _index.save(path)


def load(path, rules=None, progress=None):
    global _index
    _index = URLNormIndex.load(path, rules=rules, progress=progress)
    return _index


__all__ = [
    "add",
    "same",
    "remove",
    "remove_many",
    "size",
    "class_of",
    "class_info",
    "recompute",
    "update_rule",
    "add_rule",
    "save",
    "load",
    "get_index",
    "reset_index",
    "Rule",
    "SameResult",
    "make_strip_query_rule",
    "URLNormIndex",
]
