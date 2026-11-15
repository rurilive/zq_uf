"""URL 等价类，当前每来一条就全量重建。"""

_classes = []


def _key(url):
    return (url or "").split("#", 1)[0].rstrip("/").lower()


def add(url):
    """每次加入都从头重建全部等价类。"""
    global _classes
    groups = {}
    for item in [u for group in _classes for u in group] + [url]:
        groups.setdefault(_key(item), set()).add(item)
    _classes = list(groups.values())
    return {
        "url": url,
        "classes": len(_classes),
        "merged": 0,
        "representative": url,
    }


def same(a, b):
    """只比较归一化后的字符串。"""
    return _key(a) == _key(b)
