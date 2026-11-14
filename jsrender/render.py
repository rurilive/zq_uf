"""渲染等待，当前固定两秒。"""

WAIT = 2.0


def wait(page, condition):
    """条件只调一次，等待时间写死。"""
    return {"ok": bool(condition(page)), "waited": WAIT}
