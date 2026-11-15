"""任务窗口，当前只看时间。"""

_open = set()


def open(window):
    """到点就开，不校验上游依赖。"""
    name = window.get("name", "")
    _open.add(name)
    return {
        "window": name,
        "opened": True,
        "waited": False,
        "catch_up": False,
        "concurrency": len(_open),
    }


def close(window):
    """直接关闭，不清理未完成任务。"""
    name = window.get("name", "")
    _open.discard(name)
    return {"window": name, "closed": True, "aborted": []}
