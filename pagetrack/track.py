"""采集页面版本追踪，当前整页覆盖。"""

_pages = {}


def save(page, content):
    """每次抓取直接覆盖，不生成版本。"""
    _pages[page] = content
    return {"page": page, "version": None, "fingerprint": None, "created": True}


def changes(since):
    """没有变更日志，永远返回空。"""
    return {"since": since, "changes": [], "cursor": None}
