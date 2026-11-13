"""可查询的分区目录：元数据登记、范围查询、事件日志、启动修复。"""
import datetime
import json
import os
import shutil

ROOT = "warehouse"
META_DIR = ".catalog"
META_FILE = "partitions.json"
EVENT_LOG = "events.log"

_TRACKED_FIELDS = ("start", "end", "rows", "checksum")


def _meta_path():
    return os.path.join(ROOT, META_DIR, META_FILE)


def _log_path():
    return os.path.join(ROOT, META_DIR, EVENT_LOG)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _load_meta():
    path = _meta_path()
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_meta(meta):
    os.makedirs(os.path.dirname(_meta_path()), exist_ok=True)
    tmp = _meta_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, _meta_path())


def _log_event(event, dataset, name, detail=None):
    os.makedirs(os.path.dirname(_log_path()), exist_ok=True)
    entry = {
        "ts": _now(),
        "event": event,
        "dataset": dataset,
        "name": name,
    }
    if detail:
        entry["detail"] = detail
    with open(_log_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _check_component(field, value):
    if not isinstance(value, str) or not value:
        raise ValueError("partition %r must be a non-empty string, got %r" % (field, value))
    if value != os.path.basename(value) or value in (".", "..") or value.startswith("."):
        raise ValueError("partition %r is not a safe path component: %r" % (field, value))
    return value


def _parse_date(value, field):
    if not isinstance(value, str):
        raise ValueError("partition %r must be an ISO date string, got %r" % (field, value))
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError("partition %r is not a valid ISO date (YYYY-MM-DD): %r" % (field, value))
    return value


def _validate(partition):
    if not isinstance(partition, dict):
        raise ValueError("partition must be a dict, got %s" % type(partition).__name__)
    dataset = _check_component("dataset", partition.get("dataset"))
    name = _check_component("name", partition.get("name"))
    start = partition.get("start")
    end = partition.get("end")
    if (start is None) != (end is None):
        raise ValueError("partition 'start' and 'end' must be given together")
    if start is not None:
        start = _parse_date(start, "start")
        end = _parse_date(end, "end")
        if start > end:
            raise ValueError("partition time range is inverted: start %r > end %r" % (start, end))
    rows = partition.get("rows")
    if rows is not None and (not isinstance(rows, int) or isinstance(rows, bool) or rows < 0):
        raise ValueError("partition 'rows' must be a non-negative integer, got %r" % (rows,))
    checksum = partition.get("checksum")
    if checksum is not None and (not isinstance(checksum, str) or not checksum):
        raise ValueError("partition 'checksum' must be a non-empty string, got %r" % (checksum,))
    return dataset, name, start, end, rows, checksum


def register(partition):
    """登记分区：建目录、写元数据、记录事件。重复登记会更新元数据并记 update 事件。"""
    dataset, name, start, end, rows, checksum = _validate(partition)
    path = os.path.join(ROOT, dataset, name)
    os.makedirs(path, exist_ok=True)
    meta = _load_meta()
    records = meta.setdefault(dataset, {})
    old = records.get(name)
    record = {
        "path": path,
        "start": start,
        "end": end,
        "rows": rows,
        "checksum": checksum,
        "updated_at": _now(),
    }
    if old is None:
        records[name] = record
        _save_meta(meta)
        _log_event("register", dataset, name)
    elif any(old.get(field) != record[field] for field in _TRACKED_FIELDS):
        changed = {field: {"old": old.get(field), "new": record[field]}
                   for field in _TRACKED_FIELDS if old.get(field) != record[field]}
        records[name] = record
        _save_meta(meta)
        _log_event("update", dataset, name, detail={"changed": changed})
    return path


def list_partitions(dataset, start=None, end=None):
    """按数据集列出分区名；给定 start/end 时返回时间范围有重叠（含边界）的分区。"""
    _check_component("dataset", dataset)
    if (start is None) != (end is None):
        raise ValueError("'start' and 'end' must be given together")
    if start is not None:
        start = _parse_date(start, "start")
        end = _parse_date(end, "end")
        if start > end:
            raise ValueError("query time range is inverted: start %r > end %r" % (start, end))
    records = _load_meta().get(dataset, {})
    names = []
    for name, record in records.items():
        if start is not None:
            if record["start"] is None or record["end"] is None:
                continue
            if record["end"] < start or record["start"] > end:
                continue
        names.append(name)
    return sorted(names)


def get(dataset, name):
    """返回单个分区的元数据记录；不存在时抛 KeyError。"""
    _check_component("dataset", dataset)
    _check_component("name", name)
    record = _load_meta().get(dataset, {}).get(name)
    if record is None:
        raise KeyError("partition not found: %s/%s" % (dataset, name))
    return dict(record)


def remove(dataset, name):
    """删除分区记录和目录，并记录 remove 事件；不存在时抛 KeyError。"""
    _check_component("dataset", dataset)
    _check_component("name", name)
    meta = _load_meta()
    records = meta.get(dataset, {})
    if name not in records:
        raise KeyError("partition not found: %s/%s" % (dataset, name))
    del records[name]
    if not records:
        del meta[dataset]
    _save_meta(meta)
    shutil.rmtree(os.path.join(ROOT, dataset, name), ignore_errors=True)
    _log_event("remove", dataset, name)


def init():
    """启动入口：扫描 ROOT 下的分区目录，补登缺失的元数据记录并记 repair 事件。"""
    meta = _load_meta()
    repaired = []
    if os.path.isdir(ROOT):
        for dataset in sorted(os.listdir(ROOT)):
            base = os.path.join(ROOT, dataset)
            if dataset.startswith(".") or not os.path.isdir(base):
                continue
            records = meta.setdefault(dataset, {})
            for name in sorted(os.listdir(base)):
                if name.startswith(".") or not os.path.isdir(os.path.join(base, name)):
                    continue
                if name not in records:
                    records[name] = {
                        "path": os.path.join(ROOT, dataset, name),
                        "start": None,
                        "end": None,
                        "rows": None,
                        "checksum": None,
                        "updated_at": _now(),
                    }
                    repaired.append((dataset, name))
    if repaired or not os.path.isfile(_meta_path()):
        _save_meta(meta)
    for dataset, name in repaired:
        _log_event("repair", dataset, name)
    return repaired


def read_events():
    """按时间顺序返回事件日志条目列表。"""
    if not os.path.isfile(_log_path()):
        return []
    with open(_log_path(), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]

