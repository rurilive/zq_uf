"""按数据集配置的保留策略执行器。

- plan(): 按数据集配置生成删除计划（清单），年龄按数据内时间字段判断。
- apply(): 支持干跑；确认后分批执行，并写审计记录。

兼容旧接口：plan(root, days) 按文件修改时间返回路径列表，
apply(paths) 直接删除路径列表。
"""
import datetime
import json
import os
import time

DEFAULT_TIME_FIELD = "timestamp"
DEFAULT_TAGS_FIELD = "tags"
DEFAULT_BATCH_SIZE = 100
DEFAULT_MANIFEST_KEEP_DAYS = 30
DEFAULT_AUDIT_DIR = "retention_audit"


class Dataset:
    """单个数据集的保留策略。"""

    def __init__(self, name, root, days, min_keep=0, exempt_tags=(),
                 time_field=DEFAULT_TIME_FIELD, tags_field=DEFAULT_TAGS_FIELD,
                 batch_size=DEFAULT_BATCH_SIZE):
        if days < 0:
            raise ValueError("days must be >= 0")
        if min_keep < 0:
            raise ValueError("min_keep must be >= 0")
        self.name = name
        self.root = root
        self.days = days
        self.min_keep = min_keep
        self.exempt_tags = frozenset(exempt_tags)
        self.time_field = time_field
        self.tags_field = tags_field
        self.batch_size = max(1, batch_size)


class Plan:
    """删除计划（清单）。entries 中 delete 按时间从旧到新排序。"""

    def __init__(self, entries, created=None):
        self.created = time.time() if created is None else created
        self.entries = entries

    @property
    def paths(self):
        return [path for entry in self.entries for path in entry["delete"]]

    def batches(self, batch_size=None):
        for entry in self.entries:
            size = batch_size or entry["batch_size"]
            delete = entry["delete"]
            for start in range(0, len(delete), size):
                yield entry["dataset"], delete[start:start + size]

    def to_dict(self):
        return {
            "created": self.created,
            "delete_count": len(self.paths),
            "datasets": self.entries,
        }

    def save(self, manifest_dir):
        os.makedirs(manifest_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.created))
        path = os.path.join(
            manifest_dir, "plan-%s-%d.json" % (stamp, os.getpid()))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
        return path


def _as_dataset(config):
    if isinstance(config, Dataset):
        return config
    return Dataset(**config)


def _parse_time(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.timestamp()
    return None


def _read_record(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = json.loads(text.splitlines()[0])
        except (ValueError, IndexError):
            return None
    return data if isinstance(data, dict) else None


def _legacy_plan(root, days, now=None):
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    return [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if os.path.isfile(os.path.join(root, name))
        and os.path.getmtime(os.path.join(root, name)) < cutoff
    ]


def _plan_dataset(config, now):
    cutoff = now - config.days * 86400
    expired = []
    kept = []
    for name in sorted(os.listdir(config.root)):
        path = os.path.join(config.root, name)
        if not os.path.isfile(path):
            continue
        record = _read_record(path)
        stamp = _parse_time(record.get(config.time_field)) if record else None
        if stamp is None:
            kept.append({"path": path, "reason": "no_time_field"})
            continue
        tags = record.get(config.tags_field) or []
        if config.exempt_tags & set(tags):
            kept.append({"path": path, "reason": "exempt_tag"})
            continue
        if stamp >= cutoff:
            kept.append({"path": path, "reason": "within_retention"})
            continue
        expired.append((stamp, path))
    expired.sort(key=lambda item: item[0], reverse=True)
    for _, path in expired[:config.min_keep]:
        kept.append({"path": path, "reason": "min_keep"})
    delete = [path for _, path in reversed(expired[config.min_keep:])]
    return {
        "dataset": config.name,
        "root": config.root,
        "days": config.days,
        "batch_size": config.batch_size,
        "delete": delete,
        "kept": kept,
    }


def _prune_manifests(manifest_dir, keep_days, now):
    cutoff = now - keep_days * 86400
    for name in os.listdir(manifest_dir):
        path = os.path.join(manifest_dir, name)
        if not (name.startswith("plan-") and name.endswith(".json")):
            continue
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)


def plan(root=None, days=30, *, datasets=None, now=None, manifest_dir=None,
         manifest_keep_days=DEFAULT_MANIFEST_KEEP_DAYS):
    """生成删除计划。

    旧接口：plan(root, days) 按文件修改时间返回路径列表。
    新接口：plan(datasets=[...]) 或 plan([...]) 返回 Plan 清单；
    传 manifest_dir 时把清单落盘，并清理超过 manifest_keep_days 的旧清单。
    """
    if datasets is None:
        if isinstance(root, (list, tuple)):
            datasets = root
        else:
            return _legacy_plan(root, days, now)
    now = time.time() if now is None else now
    configs = [_as_dataset(item) for item in datasets]
    result = Plan([_plan_dataset(config, now) for config in configs],
                  created=now)
    if manifest_dir:
        result.save(manifest_dir)
        _prune_manifests(manifest_dir, manifest_keep_days, now)
    return result


def _write_audit(audit_dir, plan_obj, batches, dry_run, now):
    os.makedirs(audit_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    path = os.path.join(
        audit_dir, "audit-%s-%d.json" % (stamp, os.getpid()))
    statuses = [r for _, results in batches for r in results]
    record = {
        "time": now,
        "dry_run": dry_run,
        "removed": sum(1 for r in statuses if r["status"] == "removed"),
        "would_remove": sum(1 for r in statuses
                            if r["status"] == "would_remove"),
        "failed": sum(1 for r in statuses if r["status"] == "error"),
        "batches": [
            {"dataset": dataset, "results": results}
            for dataset, results in batches
        ],
        "manifest": plan_obj.to_dict() if isinstance(plan_obj, Plan) else None,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def apply(plan, *, dry_run=False, confirm=False, audit_dir=None,
          batch_size=None, now=None):
    """执行计划，返回删除（或干跑中待删）的路径列表。

    Plan 对象实际删除前必须 confirm=True，否则请先 dry_run=True 干跑。
    删除按数据集分批进行；audit_dir 缺省时写到 ./retention_audit。
    旧接口：apply([path, ...]) 直接删除，不写审计。
    """
    now = time.time() if now is None else now
    if isinstance(plan, Plan):
        if not dry_run and not confirm:
            raise ValueError(
                "refusing to delete without confirm=True; "
                "run with dry_run=True first")
        batches = list(plan.batches(batch_size))
        if audit_dir is None:
            audit_dir = DEFAULT_AUDIT_DIR
    else:
        size = batch_size or DEFAULT_BATCH_SIZE
        paths = list(plan)
        batches = [(None, paths[start:start + size])
                   for start in range(0, len(paths), size)]

    removed = []
    results = []
    for dataset, batch in batches:
        batch_results = []
        for path in batch:
            if dry_run:
                status = "would_remove"
                removed.append(path)
            else:
                try:
                    os.remove(path)
                    status = "removed"
                    removed.append(path)
                except FileNotFoundError:
                    status = "missing"
                except OSError as exc:
                    status = "error"
                    batch_results.append({"path": path, "status": status,
                                          "error": str(exc)})
                    continue
            batch_results.append({"path": path, "status": status})
        results.append((dataset, batch_results))

    if isinstance(plan, Plan):
        _write_audit(audit_dir, plan, results, dry_run, now)
    elif audit_dir is not None:
        _write_audit(audit_dir, None, results, dry_run, now)
    return removed

