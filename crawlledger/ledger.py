"""幂等采集账本。

按批次隔离的追加日志：每条记录带批次、序号和校验和，重复写入幂等；
写入先落临时文件再原子改名，失败回滚到上一个完整块；
校验可发现半行和坏块，截断修复后与页面快照对账。
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import time

_configured_dir = None
_process_dir = None
_cache = {}  # batch -> 已提交条目列表（进程内缓存）
_locks = {}  # batch -> threading.Lock
_guard = threading.Lock()

_BATCH_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_LOCK_STALE_SECONDS = 60.0


def configure(ledger_dir):
    """指定账本目录；传 None 恢复默认（进程级临时目录）。"""
    global _configured_dir
    with _guard:
        _configured_dir = ledger_dir
        _cache.clear()


def reset():
    """清空进程内缓存，模拟重启后从磁盘恢复。"""
    with _guard:
        _cache.clear()


def _dir():
    global _process_dir
    if _configured_dir:
        return _configured_dir
    env = os.environ.get("CRAWLLEDGER_DIR")
    if env:
        return env
    with _guard:
        if _process_dir is None:
            _process_dir = tempfile.mkdtemp(prefix="crawlledger-")
        return _process_dir


def _check_batch(batch):
    if not isinstance(batch, str) or not _BATCH_NAME.match(batch):
        raise ValueError("非法批次名: %r" % (batch,))


def _path(batch):
    return os.path.join(_dir(), "batch-%s.jsonl" % batch)


def _lock_for(batch):
    with _guard:
        return _locks.setdefault(batch, threading.Lock())


class _FileLock:
    """跨进程锁文件（O_EXCL 创建），带超时和 stale 清理。"""

    def __init__(self, path, timeout=10.0):
        self.path = path + ".lock"
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > _LOCK_STALE_SECONDS:
                        os.unlink(self.path)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("等待账本锁超时: " + self.path)
                time.sleep(0.01)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


def _entry_checksum(entry):
    body = {k: v for k, v in entry.items() if k != "checksum"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _serialize(entries):
    """条目行 + 提交行交替成块，提交行携带链式校验和。"""
    lines = []
    prev = ""
    for entry in entries:
        line = json.dumps(dict(entry, type="entry"), sort_keys=True, ensure_ascii=False)
        chain = hashlib.sha256((prev + line).encode("utf-8")).hexdigest()
        commit = json.dumps(
            {"type": "commit", "seq": entry["seq"], "checksum": chain},
            sort_keys=True,
        )
        lines.append(line)
        lines.append(commit)
        prev = chain
    return "\n".join(lines) + "\n" if lines else ""


def _parse(text):
    """解析并校验账本内容。

    返回 (entries, half, dropped, ok)：entries 为最后一个完整块之前的有效条目，
    half 为半行数，dropped 为回滚丢弃的行数，ok 表示内容是否无需修复。
    """
    if not text:
        return [], 0, 0, True
    entries = []
    prev = ""
    half = 0
    dropped = 0
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    else:
        half = 1
        lines.pop()  # 末尾半行
    i = 0
    expect_seq = 1
    while i < len(lines):
        line = lines[i]
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        bad = not isinstance(rec, dict) or rec.get("type") != "entry"
        if not bad:
            body = {k: v for k, v in rec.items() if k != "type"}
            bad = (
                body.get("seq") != expect_seq
                or not isinstance(body.get("checksum"), str)
                or _entry_checksum(body) != body["checksum"]
            )
        if bad:
            dropped += len(lines) - i  # 坏块：其后内容整体回滚
            break
        if i + 1 >= len(lines):
            dropped += 1  # 缺提交行的半块
            break
        try:
            commit = json.loads(lines[i + 1])
        except ValueError:
            commit = None
        chain = hashlib.sha256((prev + line).encode("utf-8")).hexdigest()
        if (
            not isinstance(commit, dict)
            or commit.get("type") != "commit"
            or commit.get("seq") != expect_seq
            or commit.get("checksum") != chain
        ):
            dropped += len(lines) - i
            break
        entries.append(body)
        prev = chain
        expect_seq += 1
        i += 2
    ok = half == 0 and dropped == 0
    return entries, half, dropped, ok


def _load(batch):
    if batch in _cache:
        return list(_cache[batch])
    entries = []
    path = _path(batch)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            entries, _, _, _ = _parse(fh.read())
    _cache[batch] = entries
    return list(entries)


def _write_atomic(batch, entries):
    """先写临时文件再原子改名；失败时旧文件不动，即回滚到上一个完整块。"""
    path = _path(batch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_serialize(entries))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append(entry):
    """追加一条采集记录。

    幂等键为 (batch, seq)：同批次同序号且内容一致的重复写入判重跳过；
    同序号内容冲突或序号跳跃抛 ValueError；不显式给 seq 时自动连续编号。
    """
    if not isinstance(entry, dict):
        raise TypeError("entry 必须是 dict")
    entry = dict(entry)
    batch = entry.get("batch")
    _check_batch(batch)
    os.makedirs(_dir(), exist_ok=True)
    with _lock_for(batch):
        with _FileLock(_path(batch)):
            _cache.pop(batch, None)  # 锁内强制重读，避免并发互相覆盖
            entries = _load(batch)
            seq = entry.get("seq")
            if seq is None:
                seq = entries[-1]["seq"] + 1 if entries else 1
                entry["seq"] = seq
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                raise ValueError("非法序号: %r" % (seq,))
            existing = next((e for e in entries if e["seq"] == seq), None)
            checksum = _entry_checksum(entry)
            if existing is not None:
                if existing["checksum"] == checksum:
                    return {
                        "appended": False,
                        "duplicate": True,
                        "total": len(entries),
                        "repaired": 0,
                        "seq": seq,
                    }
                raise ValueError("序号冲突: batch=%s seq=%s" % (batch, seq))
            expected = entries[-1]["seq"] + 1 if entries else 1
            if seq != expected:
                raise ValueError("序号不连续: 期望 %s，实际 %s" % (expected, seq))
            entry["checksum"] = checksum
            new_entries = entries + [entry]
            _write_atomic(batch, new_entries)
            _cache[batch] = new_entries
            return {
                "appended": True,
                "duplicate": False,
                "total": len(new_entries),
                "repaired": 0,
                "seq": seq,
            }


def verify(batch, snapshots=None):
    """校验一个批次：发现半行和坏块则截断修复，并可与页面快照对账。

    snapshots 为 {page: 快照校验和}，条目中的 snapshot 字段与之比对。
    返回 rows/half/repaired/ok 及已完成页面列表；ok 表示修复后的账本一致。
    """
    _check_batch(batch)
    path = _path(batch)
    entries, half, repaired = [], 0, 0
    with _lock_for(batch):
        if os.path.exists(path):
            with _FileLock(path):
                with open(path, encoding="utf-8") as fh:
                    entries, half, repaired, ok = _parse(fh.read())
                if not ok:
                    _write_atomic(batch, entries)  # 修复：截断到最后一个完整块
        _cache[batch] = entries
    mismatched = []
    missing = []
    if snapshots is not None:
        seen = set()
        for entry in entries:
            page = entry.get("page")
            seen.add(page)
            if snapshots.get(page) != entry.get("snapshot"):
                mismatched.append(page)
        missing = [p for p in snapshots if p not in seen]
    return {
        "batch": batch,
        "rows": len(entries),
        "half": half,
        "repaired": repaired,
        "ok": not mismatched and not missing,
        "pages": [e.get("page") for e in entries],
        "mismatched": mismatched,
        "missing": missing,
    }


def ledger_size():
    """账本大小随时可查：目录、字节数、批次数、条目数。"""
    root = _dir()
    total_bytes = 0
    batches = 0
    entries = 0
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not (name.startswith("batch-") and name.endswith(".jsonl")):
                continue
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            batches += 1
            total_bytes += os.path.getsize(path)
            with open(path, encoding="utf-8") as fh:
                batch_entries, _, _, _ = _parse(fh.read())
            entries += len(batch_entries)
    return {"dir": root, "bytes": total_bytes, "batches": batches, "entries": entries}
