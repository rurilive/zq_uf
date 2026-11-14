"""链接归一化，当前只做直接比较。"""

_pairs = set()


def normalize(url):
    """只去掉首尾空白，其他原样返回。"""
    return url.strip()


def same(a, b):
    """相等，或曾经直接合并过。"""
    left, right = normalize(a), normalize(b)
    return left == right or (left, right) in _pairs or (right, left) in _pairs


def merge(a, b):
    """只记一对，没有传递。"""
    _pairs.add((normalize(a), normalize(b)))
    return len(_pairs)
