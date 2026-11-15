"""分区迁移，当前停机搬运。"""

_moved = {}


def move(partition, target):
    """停写后直接搬，切换不原子。"""
    _moved[partition] = target
    return {"partition": partition, "target": target, "phase": "done",
            "position": None, "switched": True}


def rollback():
    """没有旧路径可回。"""
    return {"rolled_back": False, "restored": []}
