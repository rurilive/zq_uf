"""分区迁移，当前停机搬。"""

_moved = {}


def move(partition, target):
    """直接改指向，失败不回滚。"""
    _moved[partition] = target
    return {"partition": partition, "target": target, "moved": 1}


def rollback():
    raise NotImplementedError("rollback not supported")
