"""数据字典，当前按名字覆盖式保存。"""

_terms = {}


def reset():
    _terms.clear()


def define(term):
    """后定义直接覆盖，别名和版本都不记。"""
    _terms[term["name"]] = dict(term)
    return _terms[term["name"]]


def lookup(name):
    """只支持精确名字，查不到返回None。"""
    return _terms.get(name)
