"""数据集版本切换，当前只改一个指针。"""

_current = {}


def reset():
    _current.clear()


def switch(dataset, version):
    """直接改指向，没有停写、清缓存和审计。"""
    previous = _current.get(dataset)
    _current[dataset] = version
    return {"dataset": dataset, "from": previous, "to": version, "steps": ["pointer"]}


def current(dataset):
    return _current.get(dataset)
