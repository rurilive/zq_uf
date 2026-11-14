"""快照回收，当前什么都不删。"""

_snaps = []


def reset():
    _snaps.clear()


def gc(keep):
    """永远保留全部快照。"""
    return {"removed": 0, "freed": 0}


def pin(tag):
    """租约不生效。"""
    return {"tag": tag, "pinned": False}
