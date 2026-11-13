"""带保护的批处理历史分区清理。

分区身份不依赖目录名：优先读取分区目录内 ``partition.json`` 的时间字段，
读不到时回退到目录修改时间。删除前检查 ``.leases`` 租约标记，按批提交，
并把计划、删除、跳过和批次提交记录写入 JSONL 审计日志。
"""
import json
import os
import shutil
import time
from datetime import datetime

METADATA_NAME = "partition.json"
LEASE_DIR = ".leases"
DEFAULT_BATCH_SIZE = 100
DEFAULT_KEEP_DAYS = 30
DEFAULT_DATASET = "default"
TIME_FIELDS = ("event_time", "timestamp", "date")

REASON_LEASE_ACTIVE = "lease_active"
REASON_NOT_FOUND = "not_found"
REASON_DELETE_FAILED = "delete_failed"


class Plan(list):
    """清理计划，本身是待删路径列表，附加条目和分批等元数据。"""

    def __init__(self, entries, root, keep_days, batch_size, audit_path=None,
                 cutoffs=None):
        super().__init__(entry["path"] for entry in entries)
        self.entries = entries
        self.root = root
        self.keep_days = keep_days
        self.batch_size = batch_size
        self.audit_path = audit_path
        self.cutoffs = cutoffs or {}
        self.batches = [
            entries[index:index + batch_size]
            for index in range(0, len(entries), batch_size)
        ]


class ApplyResult(list):
    """执行结果，本身是实际删除路径列表，附加跳过和批次信息。"""

    def __init__(self, audit_path=None):
        super().__init__()
        self.skipped = []
        self.batches = []
        self.audit_path = audit_path


def _parse_time_value(value):
    """把 epoch 秒（数字或数字字符串）或 ISO 8601 字符串转成 epoch 秒。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(
                text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _dataset_from_name(name):
    for part in name.replace(os.sep, "/").split("/"):
        if part.startswith("dataset="):
            value = part.split("=", 1)[1].strip()
            if value:
                return value
    return None


def _read_partition(path):
    """读取分区时间和数据集，时间来自元数据字段，不依赖目录名。"""
    name = os.path.basename(path)
    meta_path = os.path.join(path, METADATA_NAME)
    meta = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError):
            meta = {}

    data_time = None
    for field in TIME_FIELDS:
        if field in meta:
            data_time = _parse_time_value(meta[field])
            if data_time is not None:
                break
    if data_time is None:
        data_time = os.path.getmtime(path)
        time_source = "mtime"
    else:
        time_source = "metadata"

    dataset = (meta.get("dataset") or _dataset_from_name(name)
               or DEFAULT_DATASET)
    return {
        "path": path,
        "name": name,
        "dataset": dataset,
        "data_time": data_time,
        "time_source": time_source,
    }


def _resolve_keep_days(keep_days, dataset):
    if isinstance(keep_days, dict):
        if dataset in keep_days:
            return keep_days[dataset]
        return keep_days.get(DEFAULT_DATASET, DEFAULT_KEEP_DAYS)
    return keep_days


def _active_leases(path, now):
    """返回分区上仍有效的租约；无法解析的租约按有效处理（失败保护）。"""
    lease_dir = os.path.join(path, LEASE_DIR)
    active = []
    if not os.path.isdir(lease_dir):
        return active
    for lease_name in sorted(os.listdir(lease_dir)):
        lease_path = os.path.join(lease_dir, lease_name)
        if not os.path.isfile(lease_path):
            continue
        reader = os.path.splitext(lease_name)[0]
        expires_at = None
        try:
            with open(lease_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                reader = payload.get("reader") or reader
                expires_at = _parse_time_value(payload.get("expires_at"))
        except (OSError, ValueError):
            expires_at = None
        if expires_at is None or expires_at > now:
            active.append({
                "reader": reader,
                "lease_file": lease_path,
                "expires_at": expires_at,
            })
    return active


def _append_audit(audit_path, record):
    if not audit_path:
        return
    parent = os.path.dirname(os.path.abspath(audit_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False,
                                sort_keys=True) + "\n")


def plan(root, keep_days=DEFAULT_KEEP_DAYS, batch_size=DEFAULT_BATCH_SIZE,
         audit_path=None):
    """生成清理计划。

    ``keep_days`` 为整数时对所有数据集生效；为字典时按数据集覆盖，
    ``default`` 键作为兜底。返回值既是待删路径列表，也携带完整计划元数据。
    """
    now = time.time()
    entries = []
    cutoffs = {}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        entry = _read_partition(path)
        days = _resolve_keep_days(keep_days, entry["dataset"])
        cutoff = now - days * 86400
        cutoffs[entry["dataset"]] = cutoff
        if entry["data_time"] < cutoff:
            entry["keep_days"] = days
            entries.append(entry)

    entries.sort(key=lambda item: (item["data_time"], item["path"]))
    planned = Plan(entries, root, keep_days, batch_size, audit_path, cutoffs)
    _append_audit(audit_path, {
        "event": "plan",
        "ts": now,
        "root": os.path.abspath(root),
        "keep_days": keep_days,
        "batch_size": batch_size,
        "cutoffs": cutoffs,
        "candidates": [
            {
                "path": entry["path"],
                "dataset": entry["dataset"],
                "data_time": entry["data_time"],
                "time_source": entry["time_source"],
            }
            for entry in entries
        ],
    })
    return planned


def _coerce_entries(planned):
    if isinstance(planned, Plan):
        return list(planned.entries), planned.batch_size, planned.audit_path
    entries = []
    for path in planned:
        if isinstance(path, dict):
            entries.append(dict(path))
        else:
            entries.append({"path": path})
    return entries, DEFAULT_BATCH_SIZE, None


def apply(planned, batch_size=None, audit_path=None):
    """按批执行清理计划，删除前检查租约，返回删除和跳过明细。

    兼容旧接口：传入纯路径列表时按默认批次大小执行，返回值仍是
    实际删除路径列表。
    """
    entries, default_size, plan_audit = _coerce_entries(planned)
    size = batch_size or default_size
    audit = audit_path or plan_audit
    result = ApplyResult(audit)
    now = time.time()
    batches = [
        entries[index:index + size]
        for index in range(0, len(entries), size)
    ]

    for batch_index, batch in enumerate(batches, start=1):
        batch_record = {
            "index": batch_index,
            "removed": [],
            "skipped": [],
        }
        for entry in batch:
            path = entry["path"]
            if not os.path.exists(path):
                skipped = {
                    "path": path,
                    "reason": REASON_NOT_FOUND,
                    "detail": "partition directory missing before apply",
                }
            else:
                leases = _active_leases(path, now)
                if leases:
                    skipped = {
                        "path": path,
                        "reason": REASON_LEASE_ACTIVE,
                        "detail": leases,
                    }
                else:
                    try:
                        shutil.rmtree(path)
                    except OSError as exc:
                        skipped = {
                            "path": path,
                            "reason": REASON_DELETE_FAILED,
                            "detail": str(exc),
                        }
                    else:
                        skipped = None
            if skipped is None:
                result.append(path)
                batch_record["removed"].append(path)
                _append_audit(audit, {
                    "event": "delete",
                    "ts": time.time(),
                    "batch": batch_index,
                    "path": path,
                    "dataset": entry.get("dataset"),
                    "data_time": entry.get("data_time"),
                })
            else:
                result.skipped.append(skipped)
                batch_record["skipped"].append(skipped)
                _append_audit(audit, {
                    "event": "skip",
                    "ts": time.time(),
                    "batch": batch_index,
                    "path": skipped["path"],
                    "reason": skipped["reason"],
                    "detail": skipped["detail"],
                })
        result.batches.append(batch_record)
        _append_audit(audit, {
            "event": "batch",
            "ts": time.time(),
            "batch": batch_index,
            "removed_count": len(batch_record["removed"]),
            "skipped_count": len(batch_record["skipped"]),
            "removed": batch_record["removed"],
            "skipped_paths":
                [item["path"] for item in batch_record["skipped"]],
        })
    return result
