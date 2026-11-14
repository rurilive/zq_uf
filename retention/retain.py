"""数据保留：按保留期找过期分区，沿血缘级联删除，租约保护，分批提交并审计。

纯 Python 标准库实现，对外只暴露 :func:`plan` 和 :func:`apply` 两个入口，
另有 :class:`RetentionError` 与 :class:`AuditLog` 供调用方处理失败与审计。
"""

import json
import time
from collections import deque

ACTIVE_LEASE = "active_lease"


class RetentionError(RuntimeError):
    """批次提交失败时抛出，``partial`` 携带失败前已提交的结果与审计。"""

    def __init__(self, message, partial=None):
        super().__init__(message)
        self.partial = partial or {}


class AuditLog:
    """内存审计日志，传入 ``path`` 时同时按 JSON Lines 追加落盘。"""

    def __init__(self, path=None):
        self.path = path
        self.entries = []

    def append(self, entry):
        record = dict(entry)
        record["ts"] = round(time.time(), 6)
        self.entries.append(record)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record


def _ref(table, partition):
    return {"table": table, "partition": partition}


def _ref_key(ref):
    return ref["table"], ref["partition"]


def _primary_ref(dataset, partition):
    return _ref(partition.get("table", dataset), partition["name"])


def _as_refs(items):
    """把下游描述统一成 ``{"table", "partition"}`` 列表。"""
    refs = []
    for item in items or ():
        if isinstance(item, dict):
            refs.append(_ref(item["table"], item.get("partition", item.get("name"))))
        else:
            table, partition = item
            refs.append(_ref(table, partition))
    return refs


def _lookup(mapping, key):
    """血缘/租约表的键兼容 tuple 与 list。"""
    value = mapping.get(key)
    if value is None:
        for candidate, candidate_value in mapping.items():
            if tuple(candidate) == tuple(key):
                value = candidate_value
                break
    return value


def _lease_active(lease):
    """非空租约默认活跃，显式 ``active: false`` 视为已释放。"""
    if not lease:
        return False
    if isinstance(lease, dict):
        return lease.get("active", True) is not False
    return True


def _skip_entry(ref, reason, lease=None, via=None):
    entry = {
        "table": ref["table"],
        "partition": ref["partition"],
        "reason": reason,
    }
    if lease is not None:
        entry["lease"] = lease
    if via is not None:
        entry["via"] = dict(via)
    return entry


def plan(dataset, partitions, keep_days=30, lineage=None, leases=None):
    """按保留期挑出过期分区，并沿血缘标记级联删除范围。

    - ``age_days > keep_days`` 的主表分区进 ``delete``，原样保留分区字典。
    - 从待删主表分区出发 BFS 血缘（内联 ``downstream`` 加外部 ``lineage``），
      下游分区进 ``cascade``，每项带 ``via`` 指向上游；环和重复边去重。
    - 命中活跃租约的分区进 ``skipped``（``reason="active_lease"``），且不继续
      从该分区向下遍历，租约释放后由下次清理处理整条分支。
    """
    lineage = lineage or {}
    leases = leases or {}
    partitions = list(partitions or [])

    inline_edges = {}
    expired = []
    for partition in partitions:
        ref = _primary_ref(dataset, partition)
        if partition.get("downstream"):
            inline_edges[_ref_key(ref)] = _as_refs(partition["downstream"])
        if partition.get("age_days", 0) > keep_days:
            expired.append((partition, ref))

    skipped = []
    delete = []
    roots = []
    for partition, ref in expired:
        lease = partition.get("lease")
        if lease is None:
            lease = _lookup(leases, _ref_key(ref))
        if _lease_active(lease):
            skipped.append(_skip_entry(ref, ACTIVE_LEASE, lease=lease))
            continue
        delete.append(partition)
        roots.append(ref)

    def downstream_of(ref):
        key = _ref_key(ref)
        return inline_edges.get(key, []) + _as_refs(_lookup(lineage, key))

    cascade = []
    seen = {_ref_key(ref) for ref in roots}
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        via = _ref(current["table"], current["partition"])
        for downstream in downstream_of(current):
            key = _ref_key(downstream)
            if key in seen:
                continue
            seen.add(key)
            lease = _lookup(leases, key)
            if _lease_active(lease):
                skipped.append(_skip_entry(downstream, ACTIVE_LEASE, lease=lease, via=via))
                continue
            cascade.append({
                "table": downstream["table"],
                "partition": downstream["partition"],
                "via": dict(via),
            })
            queue.append(downstream)

    result = {
        "dataset": dataset,
        "keep_days": keep_days,
        "delete": delete,
        "skipped": skipped,
    }
    if cascade:
        result["cascade"] = cascade
    return result


def _default_delete(batch):
    """默认删除为空操作，真实存储由调用方通过 ``delete_func`` 注入。"""
    return None


def apply(plan_payload, batch_size=100, interval_seconds=0.0, delete_func=None,
          sleeper=None, audit_log=None):
    """按计划分批提交删除，失败停在当前批次并写审计。

    删除目标 = 主表过期分区 + ``cascade`` 下游，按 ``batch_size`` 分批；
    批间调用 ``sleeper(interval_seconds)``（间隔为 0 时不睡眠）。每批调用
    ``delete_func([ref, ...])``，某批抛错即停止，已提交批次不回滚，改抛
    :class:`RetentionError`，其 ``partial`` 携带失败前的批次、计数与审计。
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    delete_func = delete_func or _default_delete
    sleeper = sleeper or time.sleep
    audit = audit_log or AuditLog()

    dataset = plan_payload.get("dataset")
    targets = [
        _primary_ref(dataset, partition)
        for partition in plan_payload.get("delete", [])
    ]
    targets.extend(
        _ref(item["table"], item["partition"])
        for item in plan_payload.get("cascade", [])
    )
    skipped = list(plan_payload.get("skipped", []))

    if not targets:
        return {
            "status": "completed",
            "dataset": dataset,
            "deleted": 0,
            "skipped": skipped,
        }

    audit.append({
        "event": "retention_start",
        "dataset": dataset,
        "target_count": len(targets),
        "scope": [dict(ref) for ref in targets],
        "skipped": skipped,
    })

    chunks = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    batches = []
    deleted = 0
    for batch_no, batch in enumerate(chunks, start=1):
        scope = [dict(ref) for ref in batch]
        audit.append({
            "event": "batch_start",
            "dataset": dataset,
            "batch_no": batch_no,
            "batch_size": len(batch),
            "scope": scope,
        })
        try:
            delete_func([dict(ref) for ref in batch])
        except Exception as exc:
            audit.append({
                "event": "batch_failed",
                "dataset": dataset,
                "batch_no": batch_no,
                "error": str(exc),
            })
            partial = {
                "status": "failed",
                "dataset": dataset,
                "deleted": deleted,
                "batches": batches,
                "skipped": skipped,
                "failed_batch": batch_no,
                "error": str(exc),
                "audit": list(audit.entries),
            }
            raise RetentionError(
                "retention stopped at batch %d: %s" % (batch_no, exc), partial
            ) from exc

        deleted += len(batch)
        batches.append({
            "batch_no": batch_no,
            "size": len(batch),
            "status": "committed",
            "targets": scope,
        })
        audit.append({
            "event": "batch_committed",
            "dataset": dataset,
            "batch_no": batch_no,
            "batch_size": len(batch),
            "deleted_total": deleted,
        })
        if batch_no < len(chunks) and interval_seconds > 0:
            sleeper(interval_seconds)

    return {
        "status": "completed",
        "dataset": dataset,
        "deleted": deleted,
        "batches": batches,
        "skipped": skipped,
        "audit": list(audit.entries),
    }
