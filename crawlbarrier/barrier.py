"""采集阶段屏障，当前只有一个完成标记。"""

_arrived = {}


def arrive(batch, shard, ok=True):
    """成功失败都算到达。"""
    _arrived.setdefault(batch, set()).add(shard)
    return len(_arrived[batch])


def wait(batch, timeout=None):
    """立即返回已到达分片，不等待也不报错。"""
    return sorted(_arrived.get(batch, set()))
