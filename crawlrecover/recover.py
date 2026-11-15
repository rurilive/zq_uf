"""采集崩溃恢复，当前重启后整批重跑。"""

_done = {}


def mark(task, page):
    """记下已完成页面，但没有落盘。"""
    _done.setdefault(task, set()).add(page)
    return {"task": task, "marked": page}


def recover(task):
    """恢复一律从零开始，不读已完成集合。"""
    return {"task": task, "start": 0, "skipped": [], "truncated": False}
