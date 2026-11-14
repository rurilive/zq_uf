"""快照链回收：引用计数 + 租约 + 可中断续跑。纯标准库。

口径：
- 块按引用计数管理，快照创建/备份开始时引用父块，删除快照只减引用。
- 并发备份的新块先登记（registered）再写入，提交后才变 committed；
  回收跳过已登记未完成的块。
- pin(tag) 给快照加租约，租约期内 gc 不删该快照及其块。
- 回收前先把候选块标记进 gc_pending 并落盘，逐块删除；
  中断后重启 reset(同一目录) 再 gc 会从剩余标记继续。
"""

import json
import os
import tempfile
import threading
import time

DEFAULT_LEASE_TTL = 60.0

_lock = threading.RLock()
_store_dir = None
_state = None


def _fresh_state():
    return {
        "seq": 0,
        "snapshots": {},  # name -> {"parent", "blocks": [bid], "seq"}
        "blocks": {},     # bid -> {"refs", "size", "status"}
        "backups": {},    # name -> {"parent", "base": [bid], "new": [bid], "seq"}
        "leases": {},     # tag -> expires_at
        "gc_pending": [], # 已标记待回收的 bid，逐块删除，可续跑
    }


def _state_path():
    return os.path.join(_store_dir, "state.json")


def _blocks_dir():
    return os.path.join(_store_dir, "blocks")


def _block_path(bid):
    return os.path.join(_blocks_dir(), bid)


def _save():
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_state, fh)
    os.replace(tmp, _state_path())


def _ensure():
    if _state is None:
        reset()


def reset(store_dir=None):
    """重置模块状态。给定目录则加载已有状态（用于重启续跑），否则用全新临时目录。"""
    global _store_dir, _state
    with _lock:
        if store_dir is None:
            store_dir = tempfile.mkdtemp(prefix="snapshotgc-")
        _store_dir = store_dir
        os.makedirs(_blocks_dir(), exist_ok=True)
        if os.path.exists(_state_path()):
            with open(_state_path(), encoding="utf-8") as fh:
                _state = json.load(fh)
        else:
            _state = _fresh_state()
            _save()


def _next_seq():
    _state["seq"] += 1
    return _state["seq"]


def create_snapshot(name, parent=None):
    """从父快照派生空快照，父块引用计数 +1。"""
    with _lock:
        _ensure()
        if name in _state["snapshots"] or name in _state["backups"]:
            raise ValueError("snapshot exists: %s" % name)
        blocks = []
        if parent is not None:
            blocks = list(_state["snapshots"][parent]["blocks"])
            for bid in blocks:
                _state["blocks"][bid]["refs"] += 1
        _state["snapshots"][name] = {
            "parent": parent, "blocks": blocks, "seq": _next_seq(),
        }
        _save()
        return {"name": name, "blocks": len(blocks)}


def begin_backup(name, parent=None):
    """开始并发备份：父块先登记引用，新块随 write_block 登记后再写入。"""
    with _lock:
        _ensure()
        if name in _state["snapshots"] or name in _state["backups"]:
            raise ValueError("backup exists: %s" % name)
        base = []
        if parent is not None:
            base = list(_state["snapshots"][parent]["blocks"])
            for bid in base:
                _state["blocks"][bid]["refs"] += 1
        _state["backups"][name] = {
            "parent": parent, "base": base, "new": [], "seq": _next_seq(),
        }
        _save()
        return {"backup": name, "base_blocks": len(base)}


def write_block(backup, data):
    """新块先登记（registered）再写文件，回收会跳过未提交的登记块。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    with _lock:
        _ensure()
        if backup not in _state["backups"]:
            raise KeyError(backup)
        bid = "blk-%06d" % _next_seq()
        _state["blocks"][bid] = {"refs": 1, "size": len(data), "status": "registered"}
        _state["backups"][backup]["new"].append(bid)
        _save()
    tmp = _block_path(bid) + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, _block_path(bid))
    return bid


def commit_backup(backup):
    """提交备份：登记块转 committed，备份成为快照。"""
    with _lock:
        _ensure()
        pending = _state["backups"].pop(backup)
        for bid in pending["new"]:
            _state["blocks"][bid]["status"] = "committed"
        _state["snapshots"][backup] = {
            "parent": pending["parent"],
            "blocks": pending["base"] + pending["new"],
            "seq": pending["seq"],
        }
        _save()
        return {"name": backup, "blocks": len(pending["base"]) + len(pending["new"])}


def abort_backup(backup):
    """中止备份：释放登记引用，清掉未提交的块。"""
    with _lock:
        _ensure()
        pending = _state["backups"].pop(backup)
        orphans = []
        for bid in pending["base"] + pending["new"]:
            meta = _state["blocks"].get(bid)
            if meta is None:
                continue
            meta["refs"] -= 1
            if meta["refs"] <= 0 and meta["status"] == "registered":
                orphans.append(bid)
                _state["blocks"].pop(bid, None)
        _save()
    for bid in orphans:
        _remove_block_file(bid)
    return {"aborted": backup}


def delete_snapshot(name):
    """删除快照只减引用，块归零后留给 gc 回收。"""
    with _lock:
        _ensure()
        snap = _state["snapshots"].pop(name)
        _state["leases"].pop(name, None)
        for bid in snap["blocks"]:
            _state["blocks"][bid]["refs"] -= 1
        _save()
        return {"deleted": name}


def read_snapshot(name):
    """按引用读出快照全部块；父删后子快照依然可读。"""
    with _lock:
        _ensure()
        bids = list(_state["snapshots"][name]["blocks"])
    data = {}
    for bid in bids:
        with open(_block_path(bid), "rb") as fh:
            data[bid] = fh.read()
    return data


def pin(tag, ttl=DEFAULT_LEASE_TTL):
    """给快照加租约，租约期内 gc 不删它及其块。快照不存在则 pinned=False。"""
    with _lock:
        _ensure()
        if tag not in _state["snapshots"]:
            return {"tag": tag, "pinned": False}
        expires = time.time() + ttl
        _state["leases"][tag] = expires
        _save()
        return {"tag": tag, "pinned": True, "expires_at": expires}


def _remove_block_file(bid):
    try:
        os.remove(_block_path(bid))
    except FileNotFoundError:
        pass


def _mark(keep, now):
    """标记阶段：淘汰超出 keep 且未租约的快照，把归零 committed 块写入 gc_pending。"""
    expired = [t for t, exp in _state["leases"].items() if exp <= now]
    for tag in expired:
        _state["leases"].pop(tag, None)
    leased = set(_state["leases"])

    skipped = 0
    ordered = sorted(_state["snapshots"], key=lambda n: _state["snapshots"][n]["seq"])
    victims = ordered if keep <= 0 else ordered[:-keep]
    for name in victims:
        if name in leased:
            skipped += len(_state["snapshots"][name]["blocks"])
            continue
        delete_snapshot(name)

    pending = [bid for bid in _state["gc_pending"] if bid in _state["blocks"]]
    seen = set(pending)
    for bid, meta in _state["blocks"].items():
        if meta["status"] != "committed":
            skipped += 1  # 已登记未完成，跳过
            continue
        if meta["refs"] > 0 or bid in seen:
            continue
        pending.append(bid)
        seen.add(bid)
    _state["gc_pending"] = pending
    _save()
    return skipped


def gc(keep):
    """回收：先标记再逐块删除，可中断，重启后从剩余标记续跑。

    返回 {"removed": 回收块数, "skipped": 跳过块数, "freed": 释放字节数}。
    """
    with _lock:
        _ensure()
        skipped = _mark(keep, time.time())
    removed = 0
    freed = 0
    while True:
        with _lock:
            if not _state["gc_pending"]:
                break
            bid = _state["gc_pending"].pop(0)
            meta = _state["blocks"].get(bid)
            if meta is None or meta["refs"] > 0 or meta["status"] != "committed":
                _save()
                continue
            size = meta["size"]
            _save()
        _remove_block_file(bid)
        with _lock:
            _state["blocks"].pop(bid, None)
            _save()
        removed += 1
        freed += size
    return {"removed": removed, "skipped": skipped, "freed": freed}
