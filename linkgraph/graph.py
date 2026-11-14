"""链接图，当前只保留一跳边。"""

_edges = {}


def build(pages):
    """记录每个页面的直接出链。"""
    _edges.clear()
    for url, links in pages.items():
        _edges[url] = list(links)
    return dict(_edges)


def cycles(limit=None):
    """没有环路分析，永远返回空。"""
    return []
