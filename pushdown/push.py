"""采集结果推送，当前每次都推全量。"""

_sent = []


def reset():
    _sent.clear()


def push(batch):
    """整批照发，返回条数，没有水位。"""
    rows = list(batch)
    _sent.append(rows)
    return {"pushed": len(rows), "confirmed": 0}


def resume(token=None):
    """没有断点，token 参数被忽略。"""
    return {"pushed": 0, "token": None}
