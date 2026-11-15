"""带租约的分区回收。

口径：
- pin() 登记租约（持有者 + 到期时间），回收前检查，活跃租约的分区跳过。
- 并发备份先 pin(kind="backup") 登记再写，回收跳过未完成备份。
- 租约过期自动释放（访问时收割）。
- 删除分批、批间可暂停、失败重试并记录，批次结果落盘。
- 回收后更新分区目录 catalog.json，审计按数据集汇总写 audit.jsonl。
- 数据集口径：分区名第一个 "/" 前的段，没有 "/" 归 "default"。
"""

import json
import os
import threading
import time
import uuid

__all__ = ["gc", "pin", "release", "leases", "reset", "DEFAULT_TTL"]

DEFAULT_TTL = 60.0
DEFAULT_BATCH_SIZE = 100
DEFAULT_RETRIES = 2  # 首次失败后的额外重试次数

_lock = threading.RLock()
_leases = {}  # partition -> {"holder", "until", "kind"}

_state_dir = os.environ.get("PARTGC2_STATE_DIR", ".partgc2")


def _dataset(partition):
    return partition.split("/", 1)[0] if "/" in partition else "default"


def _reap_expired(now=None):
    """收割过期租约（自动释放），返回被释放的分区名。调用方须持锁。"""
    now = time.time() if now is None else now
    expired = [p for p, lease in _leases.items() if lease["until"] <= now]
    for p in expired:
        del _leases[p]
    return expired


def pin(partition, holder="anonymous", ttl=DEFAULT_TTL, kind="read"):
    """登记租约：持有者与到期时间。并发备份用 kind="backup" 先登记再写。"""
    with _lock:
        _reap_expired()
        until = time.time() + ttl
        _leases[partition] = {"holder": holder, "until": until, "kind": kind}
        return {
            "partition": partition,
            "pinned": True,
            "holder": holder,
            "until": until,
            "kind": kind,
        }


def release(partition, holder=None):
    """释放租约（备份完成、读取结束）。holder 不匹配时不释放。"""
    with _lock:
        lease = _leases.get(partition)
        if lease is None or (holder is not None and lease["holder"] != holder):
            return {"partition": partition, "released": False}
        del _leases[partition]
        return {"partition": partition, "released": True}


def leases():
    """当前活跃租约快照（先收割过期）。"""
    with _lock:
        _reap_expired()
        return [
            {"partition": p, **lease} for p, lease in sorted(_leases.items())
        ]


def reset():
    """清空租约表，供测试与演示使用。"""
    with _lock:
        _leases.clear()


def _paused(flag):
    if flag is None:
        return False
    if callable(flag):
        return bool(flag())
    return bool(flag.is_set())  # threading.Event


def _is_leased(partition):
    with _lock:
        _reap_expired()
        return partition in _leases


def _delete_one(name, deleter, retries):
    """删除一个分区，失败重试。返回 (ok, attempts, error)。"""
    attempts = 0
    while True:
        attempts += 1
        try:
            if deleter is not None:
                deleter(name)
            return True, attempts, None
        except Exception as exc:  # noqa: BLE001 - 删除失败要记录原因
            if attempts > retries:
                return False, attempts, str(exc)


def _append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def gc(keep):
    """回收不在保留列表里的分区。

    keep 字段：
      existing   现有分区列表
      keep       保留列表
      batch_size 每批删除个数（默认 100）
      retries    删除失败额外重试次数（默认 2）
      deleter    可选删除钩子 deleter(name)，抛异常视为失败
      pause      可选 threading.Event 或返回 bool 的回调，批间检查，置位即暂停
      state_dir  批次结果 / 目录 / 审计的落盘目录

    返回 deleted / skipped / batches / leases / failed / paused / pending /
    expired / audit / run_id。
    """
    existing = list(keep.get("existing", []))
    alive = set(keep.get("keep", []))
    batch_size = max(1, int(keep.get("batch_size", DEFAULT_BATCH_SIZE)))
    retries = int(keep.get("retries", DEFAULT_RETRIES))
    deleter = keep.get("deleter")
    pause = keep.get("pause")
    state_dir = keep.get("state_dir", _state_dir)
    run_id = uuid.uuid4().hex[:12]

    with _lock:
        expired = _reap_expired()
        active = dict(_leases)

    skipped, candidates = [], []
    for name in existing:
        if name in alive:
            continue
        lease = active.get(name)
        if lease is not None:  # 活跃租约（含未完成备份）跳过
            skipped.append({"partition": name, **lease})
        else:
            candidates.append(name)

    os.makedirs(state_dir, exist_ok=True)
    batch_log = os.path.join(state_dir, "gc-batches-%s.jsonl" % run_id)

    deleted, failed = [], []
    batches = 0
    paused = False
    cursor = 0
    while cursor < len(candidates):
        if _paused(pause):  # 批间可暂停
            paused = True
            break
        chunk = candidates[cursor:cursor + batch_size]
        cursor += len(chunk)
        batch_deleted, batch_failed, batch_skipped = [], [], []
        for name in chunk:
            if _is_leased(name):  # 删除前复查：回收期间新登记的租约同样受保护
                with _lock:
                    lease = dict(_leases[name])
                batch_skipped.append({"partition": name, **lease})
                continue
            ok, attempts, error = _delete_one(name, deleter, retries)
            if ok:
                batch_deleted.append(name)
            else:
                batch_failed.append(
                    {"partition": name, "attempts": attempts, "error": error}
                )
        batches += 1
        deleted.extend(batch_deleted)
        failed.extend(batch_failed)
        skipped.extend(batch_skipped)
        _append_jsonl(batch_log, {  # 批次结果落盘
            "run_id": run_id,
            "batch": batches,
            "deleted": batch_deleted,
            "failed": batch_failed,
            "skipped": [s["partition"] for s in batch_skipped],
            "ts": time.time(),
        })

    pending = candidates[cursor:]  # 暂停后未处理的分区

    remaining = [n for n in existing if n not in set(deleted)]
    _write_json_atomic(  # 回收后更新分区目录
        os.path.join(state_dir, "catalog.json"),
        {"partitions": remaining, "run_id": run_id, "updated": time.time()},
    )

    audit = {}
    for group, key in ((deleted, "deleted"), (failed, "failed")):
        for item in group:
            name = item["partition"] if isinstance(item, dict) else item
            entry = audit.setdefault(
                _dataset(name), {"deleted": 0, "skipped": 0, "failed": 0}
            )
            entry[key] += 1
    for item in skipped:
        audit.setdefault(
            _dataset(item["partition"]), {"deleted": 0, "skipped": 0, "failed": 0}
        )["skipped"] += 1
    _append_jsonl(  # 审计按数据集汇总
        os.path.join(state_dir, "audit.jsonl"),
        {"run_id": run_id, "ts": time.time(), "datasets": audit},
    )

    return {
        "deleted": deleted,
        "skipped": skipped,
        "batches": batches,
        "leases": leases(),
        "failed": failed,
        "paused": paused,
        "pending": pending,
        "expired": expired,
        "audit": audit,
        "run_id": run_id,
    }
