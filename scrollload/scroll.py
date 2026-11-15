"""滚动加载采集，当前固定滚三次。"""


def scroll(page, collect):
    """固定滚三次，每滚一次取一批。"""
    items = []
    for _ in range(3):
        items.extend(collect())
    return {"rounds": 3, "added": len(items), "reason": "max_rounds", "seen": len(items)}


def done(page):
    """永远不算到底。"""
    return {"done": False, "reason": ""}
