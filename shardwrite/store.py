"""按日期和序号分片写入，配合清单原子发布与范围读取。

目录结构（``path`` 为存储根目录）::

    path/
      2026-03-01/
        2026-03-01-000001.jsonl
        2026-03-01-000001.manifest.json
        2026-03-01-000002.jsonl
        2026-03-01-000002.manifest.json
      unknown/                      # 没有 date 字段的记录

已发布的分片文件不可变：追加写入时先写临时文件，再用
``os.replace`` 原子改名；进程崩溃只会留下 ``.tmp`` 残留，下次写入
自动清理，不会产生半行数据。
"""

import hashlib
import json
import os
import shutil

DEFAULT_MAX_ROWS = 10000
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
NO_DATE_DIR = "unknown"
TMP_SUFFIX = ".tmp"


def _date_of(record):
    date = record.get("date")
    return date if isinstance(date, str) and date else NO_DATE_DIR


def _time_key(record):
    for field in ("timestamp", "time", "date"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _shard_paths(directory, date, seq):
    name = "%s-%06d" % (date, seq)
    data_path = os.path.join(directory, name + ".jsonl")
    manifest_path = os.path.join(directory, name + ".manifest.json")
    return data_path, manifest_path


def _cleanup_tmp(directory):
    if not os.path.isdir(directory):
        return
    for entry in os.listdir(directory):
        if entry.endswith(TMP_SUFFIX):
            try:
                os.remove(os.path.join(directory, entry))
            except FileNotFoundError:
                pass


def _write_tmp_atomically(target_path, feed):
    """在临时文件中生成目标内容，fsync 后原子改名。

    ``feed`` 接收临时文件句柄，负责写入全部内容。
    """
    tmp_path = target_path + TMP_SUFFIX
    directory = os.path.dirname(target_path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
        feed(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, target_path)


def _write_manifest(manifest_path, manifest):
    def feed(handle):
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")

    _write_tmp_atomically(manifest_path, feed)


def _load_manifests(root):
    """返回 {(日期目录, 序号): 清单}，忽略残留临时文件。"""
    manifests = {}
    if not os.path.isdir(root):
        return manifests
    for date in sorted(os.listdir(root)):
        directory = os.path.join(root, date)
        if not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            if not entry.endswith(".manifest.json"):
                continue
            with open(os.path.join(directory, entry), encoding="utf-8") as handle:
                manifest = json.load(handle)
            manifests[(date, manifest["seq"])] = manifest
    return manifests


def _publish(directory, date, seq, lines):
    data_path, manifest_path = _shard_paths(directory, date, seq)

    def feed(handle):
        for line in lines:
            handle.write(line)

    _write_tmp_atomically(data_path, feed)
    checksum = hashlib.sha256()
    byte_count = 0
    time_start = time_end = None
    for line in lines:
        encoded = line.encode("utf-8")
        checksum.update(encoded)
        byte_count += len(encoded)
        key = _time_key(json.loads(line))
        if key is not None:
            time_start = key if time_start is None else min(time_start, key)
            time_end = key if time_end is None else max(time_end, key)
    manifest = {
        "shard": os.path.basename(data_path),
        "date": None if date == NO_DATE_DIR else date,
        "seq": seq,
        "rows": len(lines),
        "bytes": byte_count,
        "checksum": checksum.hexdigest(),
        "time_start": time_start,
        "time_end": time_end,
    }
    _write_manifest(manifest_path, manifest)
    return manifest


def _split_fresh(lines, max_rows, max_bytes):
    """按行数/字节阈值把新记录切成多个分片大小的块。"""
    chunk = []
    chunk_bytes = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        full = bool(chunk) and (
            (max_rows > 0 and len(chunk) >= max_rows)
            or (max_bytes > 0 and chunk_bytes + line_bytes > max_bytes)
        )
        if full:
            yield chunk
            chunk = []
            chunk_bytes = 0
        chunk.append(line)
        chunk_bytes += line_bytes
    if chunk:
        yield chunk


def _fill_existing(directory, date, manifest, lines, max_rows, max_bytes):
    """尽量把记录并入当前（最新）分片，返回 (新清单, 未写入的行)。"""
    data_path, _ = _shard_paths(directory, date, manifest["seq"])
    accepted = []
    accepted_bytes = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        row_full = max_rows > 0 and manifest["rows"] + len(accepted) >= max_rows
        byte_full = max_bytes > 0 and manifest["bytes"] + accepted_bytes + line_bytes > max_bytes
        if accepted and (row_full or byte_full):
            break
        accepted.append(line)
        accepted_bytes += line_bytes

    if not accepted:
        return manifest, lines

    checksum = hashlib.sha256()
    time_start = manifest["time_start"]
    time_end = manifest["time_end"]
    with open(data_path, "rb") as source:
        checksum.update(source.read())
    for line in accepted:
        checksum.update(line.encode("utf-8"))
        key = _time_key(json.loads(line))
        if key is not None:
            time_start = key if time_start is None else min(time_start, key)
            time_end = key if time_end is None else max(time_end, key)

    def feed(handle):
        with open(data_path, encoding="utf-8") as source:
            shutil.copyfileobj(source, handle)
        for line in accepted:
            handle.write(line)

    _write_tmp_atomically(data_path, feed)
    manifest = dict(manifest)
    manifest.update({
        "rows": manifest["rows"] + len(accepted),
        "bytes": manifest["bytes"] + accepted_bytes,
        "checksum": checksum.hexdigest(),
        "time_start": time_start,
        "time_end": time_end,
    })
    _write_manifest(_shard_paths(directory, date, manifest["seq"])[1], manifest)
    return manifest, lines[len(accepted):]


def write(records, path, max_rows=DEFAULT_MAX_ROWS, max_bytes=DEFAULT_MAX_BYTES):
    """把记录按日期分片落盘，返回写入行数。

    每个日期目录维护序号递增的分片；当前分片达到 ``max_rows`` 或
    ``max_bytes`` 阈值时滚动到下一片。所有内容先写临时文件，再原子
    改名发布。
    """
    grouped = {}
    total = 0
    for record in records:
        date = _date_of(record)
        grouped.setdefault(date, []).append(
            json.dumps(record, ensure_ascii=False) + "\n"
        )
        total += 1

    if total == 0:
        return 0

    os.makedirs(path, exist_ok=True)
    manifests = _load_manifests(path)

    for date in sorted(grouped):
        directory = os.path.join(path, date)
        _cleanup_tmp(directory)
        date_seqs = [seq for (dir_name, seq) in manifests if dir_name == date]
        seq = (max(date_seqs) + 1) if date_seqs else 1

        current = manifests.get((date, seq - 1))
        remaining = grouped[date]
        while remaining:
            if current is not None:
                current, remaining = _fill_existing(
                    directory, date, current, remaining, max_rows, max_bytes
                )
                if remaining:
                    current = None
                continue
            for chunk in _split_fresh(remaining, max_rows, max_bytes):
                _publish(directory, date, seq, chunk)
                seq += 1
            remaining = []

    return total


def _in_range(manifest, date, start, end):
    manifest_date = manifest.get("date")
    if date is not None:
        return manifest_date == date
    if manifest_date is None:
        return start is None and end is None
    if start is not None and manifest_date < start:
        return False
    if end is not None and manifest_date > end:
        return False
    return True


def read(path, date=None, start=None, end=None, verify=True):
    """按清单定位分片并读回记录。

    ``date`` 精确匹配某一天；``start``/``end`` 按日期做闭区间范围
    过滤（ISO 日期可直接按字符串比较）。``verify`` 为真时逐片重算
    SHA-256，与清单不一致会抛出 ``ValueError``。
    """
    if start is not None and end is not None and start > end:
        raise ValueError("start 不能晚于 end")

    rows = []
    for (_, _seq), manifest in sorted(_load_manifests(path).items()):
        if not _in_range(manifest, date, start, end):
            continue
        data_path = os.path.join(path, _date_of({"date": manifest.get("date")}), manifest["shard"])
        checksum = hashlib.sha256()
        with open(data_path, encoding="utf-8") as handle:
            for line in handle:
                checksum.update(line.encode("utf-8"))
                record = json.loads(line)
                record_date = record.get("date")
                if date is not None and record_date != date:
                    continue
                if start is not None and (not isinstance(record_date, str) or record_date < start):
                    continue
                if end is not None and (not isinstance(record_date, str) or record_date > end):
                    continue
                rows.append(record)
        if verify and checksum.hexdigest() != manifest["checksum"]:
            raise ValueError("分片校验和不一致: %s" % manifest["shard"])
    return rows
