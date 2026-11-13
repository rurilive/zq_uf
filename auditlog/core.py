"""可回放的批次审计记录（纯标准库）。

两种用法：
1. 遗留计数模式：``record(batch, path)`` 追加一行 JSON，``replay(path)`` 累加
   ``ok`` / ``failed`` 条数，保持历史行为不变。
2. 批次回放模式：``record(batch)`` 记录主键级批次，``replay(batch_id, handler)``
   只重放失败主键，并对比输出修复 / 仍失败两类结果。
"""

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone

ENV_DIR = "AUDITLOG_DIR"
DEFAULT_DIRNAME = "auditlog_data"
INDEX_NAME = "index.json"
VERSION = 1

_store_dir = None


def configure(directory=None):
    """配置 / 读取批次日志目录，也可用环境变量 AUDITLOG_DIR。"""
    global _store_dir
    if directory is not None:
        _store_dir = directory
    return _store_dir


def _store_directory(directory):
    if directory:
        return directory
    if _store_dir:
        return _store_dir
    return os.environ.get(ENV_DIR, os.path.join(os.getcwd(), DEFAULT_DIRNAME))


def _now():
    return datetime.now(timezone.utc)


def _log_filename(now):
    return "audit-%s.jsonl" % now.strftime("%Y-%m-%d")


def _new_batch_id(now):
    return "b-%s-%s" % (now.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:12])


def _fingerprint(records):
    payload = json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _interpret_result(value):
    """results 映射取值：True/真值为成功；字符串或假值为失败（字符串作为错误信息）。"""
    if isinstance(value, str):
        return False, (value or None)
    if value:
        return True, None
    return False, None


def record(batch, path=None, *, handler=None, directory=None):
    """记录一批数据。

    传入 ``path`` 时为遗留计数模式，原样追加 JSON 行并返回 None；
    不传 ``path`` 时为批次模式，``batch`` 需包含非空 ``records`` 列表，
    每条记录含主键字段（默认 ``id``），结果由 ``results`` 映射或 ``handler``
    回调判定，返回全局唯一批次号。
    """
    if path is not None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(batch, ensure_ascii=False) + "\n")
        return None

    records = batch.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("record(batch) 需要非空 records 列表")
    key_field = batch.get("key", "id")
    keys = []
    for row in records:
        if not isinstance(row, dict) or key_field not in row:
            raise KeyError("记录缺少主键字段 %r: %r" % (key_field, row))
        keys.append(row[key_field])
    if len(set(keys)) != len(keys):
        raise ValueError("批次内主键重复: %r" % keys)

    results = batch.get("results")
    entries = []
    started = time.perf_counter()
    if results is not None:
        for key in keys:
            ok, error = _interpret_result(results.get(str(key), results.get(key, True)))
            entries.append({"key": key, "status": "ok" if ok else "failed",
                            "error": error})
        elapsed = float(batch.get("elapsed", 0.0))
    elif callable(handler):
        for row, key in zip(records, keys):
            try:
                ok = bool(handler(row))
                entries.append({"key": key, "status": "ok" if ok else "failed",
                                "error": None if ok else "handler 返回假值"})
            except Exception as exc:  # 单条失败不影响整批记录
                entries.append({"key": key, "status": "failed",
                                "error": str(exc) or type(exc).__name__})
        elapsed = time.perf_counter() - started
    else:
        raise ValueError("批次模式需要 results 映射或 handler 回调")

    now = _now()
    failed_keys = [item["key"] for item in entries if item["status"] == "failed"]
    entry = {
        "version": VERSION,
        "type": "batch",
        "batch_id": _new_batch_id(now),
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key_field": key_field,
        "fingerprint": _fingerprint(records),
        "total": len(records),
        "output": len(records) - len(failed_keys),
        "failed_keys": failed_keys,
        "elapsed": round(elapsed, 6),
        "records": records,
        "entries": entries,
        "replays": [],
    }
    _persist(entry, _store_directory(directory), _log_filename(now))
    return entry["batch_id"]


def _index_path(directory):
    return os.path.join(directory, INDEX_NAME)


def _load_index(directory):
    path = _index_path(directory)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _update_index(directory, batch_id, filename):
    index = _load_index(directory)
    index[batch_id] = filename
    tmp_path = _index_path(directory) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False)
    os.replace(tmp_path, _index_path(directory))


def _persist(entry, directory, filename):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, filename), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _update_index(directory, entry["batch_id"], filename)


def _scan_file(path, batch_id):
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("type") == "batch" and payload.get("batch_id") == batch_id:
                    return payload
    except OSError:
        return None
    return None


def find_batch(batch_id, directory=None):
    """按批次号跨日志文件检索，返回 (批次记录, 所在文件名)。"""
    directory = _store_directory(directory)
    indexed = _load_index(directory).get(batch_id)
    if indexed:
        entry = _scan_file(os.path.join(directory, indexed), batch_id)
        if entry is not None:
            return entry, indexed
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".jsonl"):
                continue
            entry = _scan_file(os.path.join(directory, name), batch_id)
            if entry is not None:
                _update_index(directory, batch_id, name)  # 索引自愈
                return entry, name
    raise KeyError("找不到批次: %s" % batch_id)


def _rewrite_entry(directory, filename, entry):
    path = os.path.join(directory, filename)
    kept_lines = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("batch_id") == entry["batch_id"]:
                continue
            kept_lines.append(line if line.endswith("\n") else line + "\n")
    kept_lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.writelines(kept_lines)
    os.replace(tmp_path, path)


def replay(target, handler=None, *, directory=None):
    """回放。

    传入已存在的文件路径时为遗留模式，累加各 JSON 行的 ok/failed 条数；
    传入批次号时只重放该批的失败主键，返回
    ``{batch_id, fixed, failed, retried}``，并把本次回放写入批次历史。
    """
    if os.path.isfile(target):
        fixed = 0
        failed = 0
        with open(target, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                fixed += payload.get("ok", 0)
                failed += payload.get("failed", 0)
        return {"fixed": fixed, "failed": failed}

    if not callable(handler):
        raise TypeError("replay(batch_id, handler) 需要可回调的 handler")

    directory = _store_directory(directory)
    entry, filename = find_batch(target, directory)
    key_field = entry["key_field"]
    rows_by_key = {row[key_field]: row for row in entry["records"]}
    failed_keys = list(entry["failed_keys"])

    fixed = []
    still_failed = []
    started = time.perf_counter()
    for key in failed_keys:
        try:
            ok = bool(handler(rows_by_key[key]))
        except Exception as exc:
            ok = False
            error = str(exc) or type(exc).__name__
        else:
            error = None
        if ok:
            fixed.append(key)
        else:
            still_failed.append(key)
            for item in entry["entries"]:
                if item["key"] == key:
                    item["error"] = error
    elapsed = time.perf_counter() - started

    fixed_set = set(fixed)
    failed_set = set(still_failed)
    for item in entry["entries"]:
        if item["key"] in fixed_set:
            item["status"] = "ok"
            item["error"] = None
        elif item["key"] in failed_set:
            item["status"] = "failed"
    entry["failed_keys"] = still_failed
    entry["output"] = entry["total"] - len(still_failed)
    entry["replays"].append({
        "at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixed": fixed,
        "failed": still_failed,
        "retried": len(failed_keys),
        "elapsed": round(elapsed, 6),
    })
    _rewrite_entry(directory, filename, entry)
    return {
        "batch_id": target,
        "fixed": fixed,
        "failed": still_failed,
        "retried": len(failed_keys),
    }
