"""指标告警，当前逐条发。"""

_sent = []


def emit(alert):
    """来一条发一条。"""
    _sent.append(dict(alert))
    return len(_sent)


def resolve(key):
    """恢复单独返回。"""
    return {"key": key, "resolved": True}
