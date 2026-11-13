"""数据任务告警，当前逐条发送。"""

_sent = []


def reset():
    _sent.clear()


def emit(alert):
    """来一条发一条，没有窗口和去重。"""
    row = dict(alert)
    _sent.append(row)
    return len(_sent)


def sent():
    return list(_sent)
