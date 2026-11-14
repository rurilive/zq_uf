"""审计日志分片导出：按序号或时间切片，断点续导，逐片校验，整体校验。"""
import hashlib
import json
import os

MANIFEST_SUFFIX = ".manifest.json"
SHARDS_SUFFIX = ".shards"
DEFAULT_SHARD_SIZE = 10000
STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETED = "completed"


class ExportIntegrityError(Exception):
    """整体校验失败：缺片或坏片。"""


def export(range_spec, path, fail_after_shards=None):
    """分片导出 range_spec["records"] 到 path。

    range_spec 可选键：
      shard_size                每片行数（序号切片，默认 10000）
      time_key + shard_seconds  按时间窗切片（记录需按时间有序）
    返回 {"rows", "shards", "checksum", "token", "path", "completed"}。
    fail_after_shards 仅用于演示/测试：写完 N 片后抛 RuntimeError 模拟中断，
    异常带 .token 可供 resume 续导。
    """
    slicing = _slicing_from(range_spec)
    manifest_path = path + MANIFEST_SUFFIX
    manifest = _load_manifest(manifest_path)
    fingerprint = _fingerprint(range_spec)
    if manifest is not None:
        if manifest.get("slicing") != slicing:
            raise ValueError(
                "existing export at %r uses different slicing; use a new path" % path)
        if (manifest.get("range_fingerprint") and fingerprint
                and manifest["range_fingerprint"] != fingerprint):
            raise ValueError(
                "range differs from existing export at %r; use a new path" % path)
    else:
        manifest = {
            "version": 1,
            "path": path,
            "slicing": slicing,
            "range_spec": _jsonable(range_spec),
            "range_fingerprint": fingerprint,
            "state": STATE_IN_PROGRESS,
            "shards": [],
            "overall": None,
        }
        _save_manifest(manifest_path, manifest)
    return _run(manifest_path, manifest, range_spec.get("records") or [],
                fail_after_shards)


def resume(token):
    """从断点继续：token 为 export 返回（或中断异常 .token 携带）的清单路径。"""
    manifest = None
    if isinstance(token, str) and os.path.exists(token):
        try:
            with open(token, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            manifest = None
    if not isinstance(manifest, dict) or "path" not in manifest \
            or "shards" not in manifest or "state" not in manifest:
        raise NotImplementedError("unknown or invalid resume token: %r" % (token,))
    range_spec = manifest.get("range_spec")
    if not isinstance(range_spec, dict) or "records" not in range_spec:
        raise NotImplementedError(
            "resume token does not carry a re-readable range: %r" % (token,))
    return _run(token, manifest, range_spec.get("records") or [])


def _run(manifest_path, manifest, records, fail_after_shards=None):
    path = manifest["path"]
    shards_dir = path + SHARDS_SUFFIX
    if manifest["state"] == STATE_COMPLETED:
        problems = _verify(manifest, shards_dir)
        if problems:
            raise ExportIntegrityError(_report(problems))
        return _summary(manifest_path, manifest)
    os.makedirs(shards_dir, exist_ok=True)
    entries = {entry["index"]: entry for entry in manifest["shards"]}
    seen = set()
    written = 0
    for index, shard_range, buf in _iter_shards(records, manifest["slicing"]):
        seen.add(index)
        entry = entries.get(index)
        shard_path = os.path.join(shards_dir, _shard_name(index))
        if entry and _shard_ok(shard_path, entry):
            continue  # 有效分片跳过，续导只补缺口
        rows, sha256 = _write_shard(shard_path, buf)
        entries[index] = {
            "index": index,
            "file": _shard_name(index),
            "rows": rows,
            "sha256": sha256,
            "range": shard_range,
        }
        manifest["shards"] = [entries[key] for key in sorted(entries)]
        _save_manifest(manifest_path, manifest)  # 每片落一次断点
        written += 1
        if fail_after_shards is not None and written >= fail_after_shards:
            error = RuntimeError(
                "simulated export interruption after %d shard(s)" % written)
            error.token = manifest_path
            raise error
    stale = [key for key in entries if key not in seen]
    if stale:
        raise ValueError(
            "record source no longer covers shard(s) %s; use a new path" % stale)
    problems = _verify(manifest, shards_dir)
    if problems:
        raise ExportIntegrityError(_report(problems))
    total_rows, checksum = _assemble(manifest, shards_dir, path)
    manifest["overall"] = {"rows": total_rows, "sha256": checksum}
    manifest["state"] = STATE_COMPLETED
    _save_manifest(manifest_path, manifest)
    return _summary(manifest_path, manifest)


def _slicing_from(range_spec):
    time_key = range_spec.get("time_key")
    shard_seconds = range_spec.get("shard_seconds")
    if time_key and shard_seconds:
        return {"mode": "time", "time_key": time_key,
                "shard_seconds": int(shard_seconds)}
    return {"mode": "sequence",
            "shard_size": int(range_spec.get("shard_size") or DEFAULT_SHARD_SIZE)}


def _iter_shards(records, slicing):
    """流式切片，不一次性读入内存。yield (index, range, records)。"""
    if slicing["mode"] == "sequence":
        size = slicing["shard_size"]
        buf, start, index = [], 0, 1
        for seq, record in enumerate(records):
            buf.append(record)
            if len(buf) == size:
                yield index, {"start_seq": start, "end_seq": seq}, buf
                buf, start, index = [], seq + 1, index + 1
        if buf:
            yield index, {"start_seq": start,
                          "end_seq": start + len(buf) - 1}, buf
        return
    time_key, seconds = slicing["time_key"], slicing["shard_seconds"]
    buf, window, index = [], None, 1
    for record in records:
        if time_key not in record:
            raise ValueError("record missing time key %r" % time_key)
        ts = record[time_key]
        current = int(ts) // seconds
        if window is None:
            window = current
        if current != window:
            yield index, {"start_ts": buf[0][time_key],
                          "end_ts": buf[-1][time_key]}, buf
            buf, window, index = [], current, index + 1
        buf.append(record)
    if buf:
        yield index, {"start_ts": buf[0][time_key],
                      "end_ts": buf[-1][time_key]}, buf


def _shard_name(index):
    return "%06d.jsonl" % index


def _write_shard(shard_path, buf):
    payload = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in buf
    ).encode("utf-8")
    tmp_path = shard_path + ".tmp"
    with open(tmp_path, "wb") as handle:
        handle.write(payload)
    os.replace(tmp_path, shard_path)
    return len(buf), hashlib.sha256(payload).hexdigest()


def _inspect(file_path):
    with open(file_path, "rb") as handle:
        data = handle.read()
    return data.count(b"\n"), hashlib.sha256(data).hexdigest()


def _shard_ok(shard_path, entry):
    if not os.path.exists(shard_path):
        return False
    rows, sha256 = _inspect(shard_path)
    return rows == entry["rows"] and sha256 == entry["sha256"]


def _verify(manifest, shards_dir):
    problems = []
    for entry in manifest["shards"]:
        shard_path = os.path.join(shards_dir, entry["file"])
        if not os.path.exists(shard_path):
            problems.append("missing shard %s (index %d)"
                            % (entry["file"], entry["index"]))
            continue
        rows, sha256 = _inspect(shard_path)
        if rows != entry["rows"] or sha256 != entry["sha256"]:
            problems.append("corrupt shard %s (index %d): rows/checksum mismatch"
                            % (entry["file"], entry["index"]))
    return problems


def _report(problems):
    return "export verification failed: " + "; ".join(problems)


def _assemble(manifest, shards_dir, path):
    digest = hashlib.sha256()
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as out:
        for entry in manifest["shards"]:
            with open(os.path.join(shards_dir, entry["file"]), "rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    out.write(chunk)
    os.replace(tmp_path, path)
    return sum(entry["rows"] for entry in manifest["shards"]), digest.hexdigest()


def _summary(manifest_path, manifest):
    return {
        "path": manifest["path"],
        "rows": manifest["overall"]["rows"],
        "shards": [dict(entry) for entry in manifest["shards"]],
        "checksum": manifest["overall"]["sha256"],
        "token": manifest_path,
        "completed": True,
    }


def _load_manifest(manifest_path):
    if not os.path.exists(manifest_path):
        return None
    with open(manifest_path, encoding="utf-8") as handle:
        return json.load(handle)


def _save_manifest(manifest_path, manifest):
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, manifest_path)


def _jsonable(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return None
    return value


def _fingerprint(range_spec):
    try:
        payload = json.dumps(range_spec, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
