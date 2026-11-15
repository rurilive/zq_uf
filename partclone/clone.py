"""分区克隆：一致性快照 + 断点续传 + 行数/校验和校验。

状态目录默认 ``$PARTCLONE_STATE`` 或 ``<tmp>/partclone-state``，结构：

- snapshots/<tag>.json   一致性快照（版本、行数、校验和）
- checkpoints/<tag>.json 断点（已复制行数、快照版本），落盘可恢复
- staging/<tag>.part     暂存区，提交前对查询不可见
- targets/<tag>.jsonl    克隆完成并校验通过后才对查询可见
- meta/<tag>.json        已提交分区的行数与校验和
- audit.log              校验结果审计（JSON Lines）
"""

import hashlib
import json
import os
import tempfile
import threading
import time

_LOCK = threading.RLock()
_STATE_DIR = os.environ.get("PARTCLONE_STATE") or os.path.join(
    tempfile.gettempdir(), "partclone-state"
)

MAX_RETRIES = 3
_BATCH = 50


class CloneError(Exception):
    """克隆过程中的可重试中断。"""


def _configure(state_dir=None):
    """重置状态目录（测试/演示用）。"""
    global _STATE_DIR
    with _LOCK:
        _STATE_DIR = state_dir or os.path.join(
            tempfile.gettempdir(), "partclone-state"
        )


def _safe(tag):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in tag)


def _paths(tag):
    safe = _safe(tag)
    return {
        "snapshot": os.path.join(_STATE_DIR, "snapshots", safe + ".json"),
        "checkpoint": os.path.join(_STATE_DIR, "checkpoints", safe + ".json"),
        "staging": os.path.join(_STATE_DIR, "staging", safe + ".part"),
        "target": os.path.join(_STATE_DIR, "targets", safe + ".jsonl"),
        "meta": os.path.join(_STATE_DIR, "meta", safe + ".json"),
        "audit": os.path.join(_STATE_DIR, "audit.log"),
    }


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _append_audit(record):
    os.makedirs(_STATE_DIR, exist_ok=True)
    record = dict(record, ts=time.time())
    with open(os.path.join(_STATE_DIR, "audit.log"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _checksum(rows):
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _read_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _checkpoint(p, tag, snapshot, total, copied):
    _write_json(p["checkpoint"], {
        "tag": tag,
        "copied": copied,
        "rows": total,
        "snapshot_version": (snapshot or {}).get("version"),
    })


def _copy_rows(rows, offset, p, tag, snapshot, fail_at, on_copy, source):
    """从断点 offset 继续往暂存区写；克隆期间源分区只读。"""
    if source is not None:
        source["readonly"] = True
    try:
        os.makedirs(os.path.dirname(p["staging"]), exist_ok=True)
        copied = offset
        with open(p["staging"], "a", encoding="utf-8") as fh:
            while copied < len(rows):
                fh.write(json.dumps(rows[copied], ensure_ascii=False) + "\n")
                copied += 1
                if copied % _BATCH == 0:
                    _checkpoint(p, tag, snapshot, len(rows), copied)
                if on_copy is not None:
                    on_copy(copied)
                if fail_at is not None and copied >= fail_at:
                    _checkpoint(p, tag, snapshot, len(rows), copied)
                    raise CloneError("clone interrupted at %d" % copied)
        _checkpoint(p, tag, snapshot, len(rows), copied)
        return copied
    finally:
        if source is not None:
            source["readonly"] = False


def clone(partition):
    """先取一致性快照再复制；失败清理半成品，重试从断点继续。"""
    tag = str(partition.get("tag", "clone"))
    max_retries = max(1, int(partition.get("max_retries", MAX_RETRIES)))
    fail_times = int(partition.get("fail_times", 0))
    fail_at = partition.get("fail_at")
    on_copy = partition.get("on_copy")
    source = partition.get("source")

    with _LOCK:
        if "data" in partition:
            rows = list(partition["data"])
            take_snapshot = True
        else:
            rows = ["row-%d" % i for i in range(int(partition.get("rows", 0)))]
            take_snapshot = False
        p = _paths(tag)
        total = len(rows)
        expected = _checksum(rows)

        snapshot = None
        if take_snapshot:
            prev = _read_json(p["snapshot"])
            ckpt = _read_json(p["checkpoint"])
            if (
                ckpt
                and prev
                and prev.get("checksum") == expected
                and os.path.exists(p["staging"])
            ):
                # 数据未变：复用同一份快照，从断点续传
                snapshot = prev
            else:
                snapshot = {
                    "tag": tag,
                    "version": (prev or {}).get("version", 0) + 1,
                    "rows": total,
                    "checksum": expected,
                }
                _write_json(p["snapshot"], snapshot)

        # 断点仅在同版本快照（或同口径行数）下有效，否则作废旧半成品
        copied = 0
        ckpt = _read_json(p["checkpoint"])
        if (
            ckpt
            and os.path.exists(p["staging"])
            and ckpt.get("snapshot_version") == (snapshot or {}).get("version")
            and (snapshot is not None or ckpt.get("rows") == total)
        ):
            copied = int(ckpt.get("copied", 0))
        else:
            for key in ("staging", "checkpoint"):
                if os.path.exists(p[key]):
                    os.remove(p[key])
        resumed = copied > 0

        attempts = 0
        while True:
            attempts += 1
            try:
                _copy_rows(
                    rows, copied, p, tag, snapshot,
                    fail_at if attempts <= fail_times else None,
                    on_copy, source,
                )
                break
            except CloneError:
                ckpt = _read_json(p["checkpoint"])
                copied = int(ckpt.get("copied", 0)) if ckpt else 0
                if attempts >= max_retries:
                    # 重试耗尽：清理半成品目标（不对查询可见），断点落盘留待续传
                    if os.path.exists(p["target"]):
                        os.remove(p["target"])
                    _append_audit({
                        "event": "clone_failed", "tag": tag,
                        "attempts": attempts, "copied": copied,
                    })
                    return {
                        "tag": tag, "copied": 0, "snapshot": snapshot,
                        "resumed": resumed, "status": "failed",
                        "attempts": attempts, "cleaned": True,
                        "checksum": None, "verified": False, "visible": False,
                    }

        # 克隆后校验：行数 + 校验和
        staged = _read_rows(p["staging"])
        target_rows = len(staged)
        target_checksum = _checksum(staged)
        matched = target_rows == total and target_checksum == expected
        _append_audit({
            "event": "clone_verify", "tag": tag, "matched": matched,
            "source_rows": total, "target_rows": target_rows,
            "source_checksum": expected, "target_checksum": target_checksum,
        })
        if not matched:
            # 校验不一致：中止，清理半成品，保留两侧计数
            for key in ("staging", "checkpoint"):
                if os.path.exists(p[key]):
                    os.remove(p[key])
            return {
                "tag": tag, "copied": copied, "snapshot": snapshot,
                "resumed": resumed, "status": "aborted", "attempts": attempts,
                "source_rows": total, "target_rows": target_rows,
                "checksum": None, "verified": False, "visible": False,
            }

        # 提交：目标分区对查询可见
        os.makedirs(os.path.dirname(p["target"]), exist_ok=True)
        os.replace(p["staging"], p["target"])
        if os.path.exists(p["checkpoint"]):
            os.remove(p["checkpoint"])
        _write_json(p["meta"], {
            "tag": tag, "rows": total, "checksum": expected,
            "snapshot_version": (snapshot or {}).get("version"),
        })
        return {
            "tag": tag, "copied": total, "snapshot": snapshot,
            "resumed": resumed, "status": "ok", "attempts": attempts,
            "checksum": expected, "verified": True, "visible": True,
        }


def verify(tag):
    """对比已提交目标与元数据的行数和校验和，结果写审计。"""
    tag = str(tag)
    with _LOCK:
        p = _paths(tag)
        meta = _read_json(p["meta"])
        target = _read_rows(p["target"]) if os.path.exists(p["target"]) else None
        if meta is None or target is None:
            result = {
                "tag": tag,
                "rows": (meta or {}).get("rows", 0),
                "matched": False,
                "checksum": (meta or {}).get("checksum"),
                "source_rows": (meta or {}).get("rows", 0),
                "target_rows": len(target) if target is not None else 0,
            }
        else:
            target_checksum = _checksum(target)
            matched = len(target) == meta["rows"] and target_checksum == meta["checksum"]
            result = {
                "tag": tag,
                "rows": meta["rows"],
                "matched": matched,
                "checksum": meta["checksum"],
                "source_rows": meta["rows"],
                "target_rows": len(target),
                "source_checksum": meta["checksum"],
                "target_checksum": target_checksum,
            }
        _append_audit(dict({"event": "verify"}, **result))
        return result
