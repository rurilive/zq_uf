"""分区回收，当前直接删。"""

_pinned = set()


def gc(keep):
    """把所有不在保留列表里的分区一次删掉。"""
    existing = list(keep.get("existing", []))
    alive = set(keep.get("keep", []))
    deleted = [name for name in existing if name not in alive]
    return {"deleted": deleted, "skipped": [], "batches": 1, "leases": []}


def pin(partition):
    """登记租约，但回收时不读。"""
    _pinned.add(partition)
    return {"partition": partition, "pinned": True, "until": None}
