"""表保留与归档：先写归档清单并校验，再分批删除。"""

import hashlib
import json
from copy import deepcopy

DEFAULT_BATCH_SIZE = 10

# 进程内归档区：table -> partition -> manifest
_ARCHIVES = {}
# 进程内表快照：table -> partition -> partition data
_TABLES = {}
# 恢复结果：table -> partition -> result
_RESTORE_RESULTS = {}

# 这些字段只用于控制和测试注入，不进入归档元数据。
# restore_fails 需要随清单保存，供恢复阶段模拟回迁写入异常。
_META_SKIP = {"rows", "downstream_confirmed", "archive_fails", "delete_fails"}


class ArchiveError(Exception):
    """归档清单写入或校验失败。"""


def _checksum(rows):
    payload = json.dumps(
        rows,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_size(value):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_SIZE
    return size if size > 0 else DEFAULT_BATCH_SIZE


def plan(table, partitions, keep_days=30, batch_size=DEFAULT_BATCH_SIZE):
    """按年龄挑过期分区，返回交给 apply 的计划。

    过期判断保持 ``age_days > keep_days``。分区数据会登记到表快照，
    下游确认状态和删除批次大小原样带入 apply。
    """
    expire = []
    snapshot = _TABLES.setdefault(table, {})
    for partition in partitions or []:
        item = deepcopy(partition)
        name = item.get("name")
        if name is not None:
            snapshot[name] = deepcopy(item)
        if item.get("age_days", 0) > keep_days:
            expire.append(item)
    return {
        "table": table,
        "keep_days": keep_days,
        "batch_size": _batch_size(batch_size),
        "expire": expire,
    }


def archive_manifest(table, partition):
    """按分区查询归档清单（含行数、校验和与行数据）；没有则返回 None。"""
    manifest = _ARCHIVES.get(table, {}).get(partition)
    return deepcopy(manifest) if manifest is not None else None


def validate_archive(manifest):
    """核对清单声明的行数、校验和与实际行数据是否一致。"""
    if not isinstance(manifest, dict):
        return False
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        return False
    if manifest.get("row_count") != len(rows):
        return False
    return manifest.get("checksum") == _checksum(rows)


def _write_archive(table, partition):
    """写入清单后重新读取校验，成功才返回归档摘要。"""
    name = partition.get("name")
    rows = deepcopy(partition.get("rows") or [])
    if not isinstance(rows, list):
        rows = [rows]
    metadata = {
        key: deepcopy(value)
        for key, value in partition.items()
        if key not in _META_SKIP
    }
    manifest = {
        "table": table,
        "partition": name,
        "row_count": len(rows),
        "checksum": _checksum(rows),
        "rows": rows,
        "metadata": metadata,
    }
    # 故障注入：模拟落库后校验失败。
    if partition.get("archive_fails"):
        manifest["checksum"] = "0" * 64

    bucket = _ARCHIVES.setdefault(table, {})
    bucket[name] = manifest
    if not validate_archive(bucket.get(name)):
        bucket.pop(name, None)
        raise ArchiveError("归档清单校验失败")

    return {
        "name": name,
        "row_count": manifest["row_count"],
        "checksum": manifest["checksum"],
    }


def _delete_partition(table, partition):
    bucket = _TABLES.get(table)
    if bucket is not None:
        bucket.pop(partition, None)


def apply(plan_payload):
    """按计划先归档、再分批删除。

    - 下游未确认的分区跳过并记录；
    - 归档清单写入或校验失败的分区跳过并记录，不删除；
    - 删除按 batch_size 分批，任一分区失败即停批。
    """
    payload = plan_payload or {}
    table = payload.get("table", "")
    expire = [deepcopy(item) for item in (payload.get("expire") or [])]
    batch_size = _batch_size(payload.get("batch_size"))

    archived = []
    skipped = []
    ready = []
    for partition in expire:
        name = partition.get("name")
        if not name:
            skipped.append({"name": name, "reason": "invalid_partition"})
            continue
        if not partition.get("downstream_confirmed", True):
            skipped.append({"name": name, "reason": "downstream_unconfirmed"})
            continue
        try:
            archived.append(_write_archive(table, partition))
        except ArchiveError as exc:
            skipped.append(
                {"name": name, "reason": "archive_failed", "detail": str(exc)}
            )
            continue
        ready.append(partition)

    deleted = 0
    batches = []
    stopped = False
    chunks = [
        ready[index : index + batch_size]
        for index in range(0, len(ready), batch_size)
    ]
    for number, chunk in enumerate(chunks, start=1):
        batch = {
            "batch": number,
            "partitions": [partition["name"] for partition in chunk],
            "deleted": 0,
            "status": "ok",
        }
        failed_at = None
        for index, partition in enumerate(chunk):
            if partition.get("delete_fails"):
                failed_at = index
                break
            _delete_partition(table, partition["name"])
            batch["deleted"] += 1
            deleted += 1

        if failed_at is None:
            batches.append(batch)
            continue

        # 失败停批：本批剩余分区和后续批次都不再删除。
        batch["status"] = "failed"
        batch["failed"] = chunk[failed_at]["name"]
        batch["reason"] = "delete_failed"
        batches.append(batch)
        stopped = True
        skipped.append(
            {"name": chunk[failed_at]["name"], "reason": "delete_failed"}
        )
        for partition in chunk[failed_at + 1 :]:
            skipped.append(
                {"name": partition["name"], "reason": "delete_stopped"}
            )
        for later_chunk in chunks[number:]:
            for partition in later_chunk:
                skipped.append(
                    {"name": partition["name"], "reason": "delete_stopped"}
                )
        break

    return {
        "archived": archived,
        "archived_count": len(archived),
        "deleted": deleted,
        "skipped": skipped,
        "batches": batches,
        "stopped": stopped,
    }


def _archived_names(table, partition):
    if partition is not None:
        return [partition]
    return sorted(_ARCHIVES.get(table, {}))


def _restore_one(table, name):
    manifest = _ARCHIVES.get(table, {}).get(name)
    result = {
        "table": table,
        "partition": name,
        "status": "failed",
        "verified": False,
        "row_count": None,
        "checksum": None,
        "expected_row_count": None,
        "expected_checksum": None,
        "reason": None,
    }
    if manifest is None:
        result["reason"] = "archive_missing"
        return result

    result["expected_row_count"] = manifest.get("row_count")
    result["expected_checksum"] = manifest.get("checksum")
    if not validate_archive(manifest):
        result["reason"] = "archive_checksum_mismatch"
        return result

    rows = deepcopy(manifest.get("rows") or [])
    metadata = deepcopy(manifest.get("metadata") or {})
    metadata["name"] = name
    metadata["rows"] = rows

    bucket = _TABLES.setdefault(table, {})
    previous = bucket.get(name)
    bucket[name] = metadata

    # 故障注入：模拟回迁写入缺失一行。
    if metadata.get("restore_fails"):
        metadata["rows"] = rows[:-1]

    # 回迁后重新读取核对；失败恢复目标分区原状，归档清单保留。
    current_rows = (bucket.get(name) or {}).get("rows") or []
    if (
        len(current_rows) != manifest.get("row_count")
        or _checksum(current_rows) != manifest.get("checksum")
    ):
        if previous is None:
            bucket.pop(name, None)
        else:
            bucket[name] = previous
        result["reason"] = "restore_verify_failed"
        return result

    result.update(
        {
            "status": "restored",
            "verified": True,
            "row_count": len(current_rows),
            "checksum": _checksum(current_rows),
            "reason": None,
        }
    )
    return result


def restore(table, partition=None):
    """按归档清单回迁并核对。

    ``partition`` 为 None 时恢复该表全部已归档分区。清单缺失或校验失败时
    不改动目标分区，归档清单保留。返回每个分区的恢复结果。
    """
    results = []
    for name in _archived_names(table, partition):
        result = _restore_one(table, name)
        _RESTORE_RESULTS.setdefault(table, {})[name] = result
        results.append(result)
    return results


def restore_status(table, partition=None):
    """查询恢复结果：指定分区返回单条，否则返回按分区排序的列表。"""
    stored = _RESTORE_RESULTS.get(table, {})
    if partition is not None:
        result = stored.get(partition)
        return deepcopy(result) if result is not None else None
    return [deepcopy(stored[name]) for name in sorted(stored)]


def verify_restore(table, partition=None):
    """用归档清单核对当前表分区，按分区返回核对结果。"""
    checks = []
    for name in _archived_names(table, partition):
        manifest = _ARCHIVES.get(table, {}).get(name)
        current = _TABLES.get(table, {}).get(name)
        check = {
            "table": table,
            "partition": name,
            "exists": current is not None,
            "verified": False,
            "row_count": None,
            "checksum": None,
            "expected_row_count": manifest.get("row_count") if manifest else None,
            "expected_checksum": manifest.get("checksum") if manifest else None,
            "reason": None,
        }
        if manifest is None:
            check["reason"] = "archive_missing"
        elif current is None:
            check["reason"] = "partition_missing"
        else:
            rows = current.get("rows") or []
            check["row_count"] = len(rows)
            check["checksum"] = _checksum(rows)
            check["verified"] = (
                len(rows) == manifest.get("row_count")
                and check["checksum"] == manifest.get("checksum")
            )
            if not check["verified"]:
                check["reason"] = "checksum_mismatch"
        checks.append(check)

    if partition is not None:
        return checks[0] if checks else None
    return checks


def table_data(table, partition=None):
    """查询当前表快照，便于核对回迁结果。"""
    bucket = _TABLES.get(table, {})
    if partition is not None:
        item = bucket.get(partition)
        return deepcopy(item) if item is not None else None
    return deepcopy(bucket)


def reset_state():
    """清空进程内归档区、表快照和恢复结果。"""
    _ARCHIVES.clear()
    _TABLES.clear()
    _RESTORE_RESULTS.clear()
