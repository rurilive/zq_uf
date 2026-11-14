"""分区小文件合并：读旧写新，校验通过后原子切换清单。"""
import hashlib
import json
import os
import time
import uuid

MERGED_PREFIX = "merged-"
MANIFEST = "manifest.json"
LOCK_NAME = ".compact.lock"
TMP_SUFFIX = ".tmp"

LOCK_TIMEOUT = 30.0
LOCK_STALE_AFTER = 60.0


def _data_files(partition):
    if not os.path.isdir(partition):
        return []
    return sorted(
        name
        for name in os.listdir(partition)
        if name.endswith(".jsonl") and not name.endswith(TMP_SUFFIX)
    )


def _read_rows(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _load_manifest(partition):
    path = os.path.join(partition, MANIFEST)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _count_and_checksum(path):
    digest = hashlib.sha256()
    rows = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
            rows += chunk.count(b"\n")
    return rows, digest.hexdigest()


class _PartitionLock:
    """基于锁文件的同一分区互斥，线程与进程均安全。"""

    def __init__(self, partition):
        self.path = os.path.join(partition, LOCK_NAME)

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"compact lock timeout: {self.path}")
                try:
                    if time.time() - os.path.getmtime(self.path) > LOCK_STALE_AFTER:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                time.sleep(0.01)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _switch_manifest(partition, new_manifest):
    """原子切换清单；失败回退到旧清单。"""
    path = os.path.join(partition, MANIFEST)
    backup = None
    if os.path.exists(path):
        with open(path, "rb") as handle:
            backup = handle.read()
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(new_manifest, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if backup is not None:
            with open(path, "wb") as handle:
                handle.write(backup)
        elif os.path.exists(path):
            os.remove(path)
        raise
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def compact(partition, rows):
    """读旧写新再切换：失败保留旧文件，重复合并幂等。

    返回合并前后文件数、行数、校验和与切换结果。
    """
    os.makedirs(partition, exist_ok=True)
    with _PartitionLock(partition):
        files_before = _data_files(partition)
        rows_before = len(query(partition))
        old_manifest = _load_manifest(partition)

        tmp_path = os.path.join(
            partition, f"{MERGED_PREFIX}{uuid.uuid4().hex}{TMP_SUFFIX}"
        )
        final_path = None
        final_name = None
        written = 0
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
                    written += 1
                handle.flush()
                os.fsync(handle.fileno())

            checked_rows, checksum = _count_and_checksum(tmp_path)
            if checked_rows != written:
                raise IOError(
                    f"row count mismatch: wrote {written}, read back {checked_rows}"
                )

            final_name = f"{MERGED_PREFIX}{checksum[:16]}.jsonl"
            final_path = os.path.join(partition, final_name)
            already_current = (
                old_manifest
                and old_manifest.get("file") == final_name
                and old_manifest.get("rows") == written
                and old_manifest.get("checksum") == checksum
                and os.path.exists(final_path)
            )
            if already_current:
                os.remove(tmp_path)
                switched = False
            else:
                os.replace(tmp_path, final_path)
                _switch_manifest(
                    partition,
                    {"file": final_name, "rows": written, "checksum": checksum},
                )
                switched = True
        except Exception:
            for path in (tmp_path, final_path):
                if path and os.path.exists(path):
                    os.remove(path)
            raise

        if switched:
            for name in files_before:
                if name != final_name:
                    try:
                        os.remove(os.path.join(partition, name))
                    except OSError:
                        pass

        return {
            "partition": partition,
            "file": final_name,
            "files_before": len(files_before),
            "files_after": len(_data_files(partition)),
            "rows_before": rows_before,
            "rows_after": written,
            "checksum": checksum,
            "switched": switched,
        }


def query(partition):
    """按清单读当前合并文件；无清单时读全部旧分片（切换前）。"""
    manifest = _load_manifest(partition)
    if manifest:
        path = os.path.join(partition, manifest["file"])
        return _read_rows(path) if os.path.exists(path) else []
    rows = []
    for name in _data_files(partition):
        rows.extend(_read_rows(os.path.join(partition, name)))
    return rows
