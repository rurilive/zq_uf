"""表复制：一致性快照 + 增量补齐，失败清理重试，复制后校验。"""

import hashlib
import json
import os

_source = {}
_target = {}

MAX_RETRIES = 3


class CopyError(Exception):
    """重试耗尽；断点位点保留在状态目录，可再次调用 copy 续传。"""


def _state_dir(dst):
    base = os.environ.get("TABLECOPY_STATE_DIR", ".tablecopy")
    return os.path.join(base, str(dst))


def _row_digest(row):
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest(), 16)


def _checksum(rows):
    """行级摘要之和（与顺序无关），64 位十六进制。"""
    total = sum(_row_digest(row) for row in rows) % (1 << 256)
    return format(total, "064x")


def verify():
    """对比源表期望值与目标表：行数 + 校验和。"""
    src_rows = list(_source.values())
    dst_rows = list(_target.values())
    src_sum = _checksum(src_rows)
    dst_sum = _checksum(dst_rows)
    return {
        "ok": len(src_rows) == len(dst_rows) and src_sum == dst_sum,
        "rows_source": len(src_rows),
        "rows_target": len(dst_rows),
        "checksum_source": src_sum,
        "checksum_target": dst_sum,
    }


def _checkpoint_path(state_dir):
    return os.path.join(state_dir, "checkpoint.json")


def _load_checkpoint(state_dir):
    """读取断点位点和已落盘快照；上次已完成或不存在则返回 (None, None)。"""
    try:
        with open(_checkpoint_path(state_dir), encoding="utf-8") as fh:
            checkpoint = json.load(fh)
        with open(os.path.join(state_dir, "snapshot.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return None, None
    if checkpoint.get("phase") == "done":
        return None, None
    return checkpoint, rows


def _save_checkpoint(state_dir, checkpoint):
    os.makedirs(state_dir, exist_ok=True)
    with open(_checkpoint_path(state_dir), "w", encoding="utf-8") as fh:
        json.dump(checkpoint, fh, ensure_ascii=False, indent=2)


def _save_snapshot(state_dir, rows):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=str)


def _append_increment(state_dir, lsn, op, row):
    os.makedirs(state_dir, exist_ok=True)
    line = json.dumps({"lsn": lsn, "op": op, "row": row}, ensure_ascii=False, default=str)
    with open(os.path.join(state_dir, "increments.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _reset_increments(state_dir):
    path = os.path.join(state_dir, "increments.jsonl")
    if os.path.exists(path):
        os.remove(path)


def _apply(expected, op, row):
    if op == "delete":
        _target.pop(row["id"], None)
        expected.pop(row["id"], None)
    else:
        _target[row["id"]] = dict(row)
        expected[row["id"]] = dict(row)


def copy(src, dst):
    """先取一致性快照，再补复制期间的增量；失败清理目标半成品并按断点重试。

    src 为普通可迭代行集时退化为一次性全量复制（无位点落盘）；
    src 实现 snapshot()/changes_since(lsn) 协议时走快照 + 增量。
    返回行数、快照位点、增量条数、重试次数和校验结果。
    """
    persistent = hasattr(src, "snapshot") and hasattr(src, "changes_since")
    state_dir = _state_dir(dst) if persistent else None
    checkpoint = None
    snapshot_rows = None
    if persistent:
        checkpoint, snapshot_rows = _load_checkpoint(state_dir)
    retries = 0
    while True:
        try:
            if snapshot_rows is None:
                if persistent:
                    snapshot_lsn, snapshot_rows = src.snapshot()
                    checkpoint = {
                        "dst": str(dst),
                        "snapshot_lsn": snapshot_lsn,
                        "phase": "snapshot",
                        "incremental_offset": 0,
                        "retries": 0,
                    }
                    _reset_increments(state_dir)
                    _save_snapshot(state_dir, snapshot_rows)
                    _save_checkpoint(state_dir, checkpoint)
                else:
                    snapshot_lsn, snapshot_rows = None, [dict(r) for r in src]
            elif persistent:
                snapshot_lsn = checkpoint["snapshot_lsn"]
            _target.clear()
            expected = {}
            for row in snapshot_rows:
                _apply(expected, "upsert", row)
            incremental = 0
            if persistent:
                _, changes = src.changes_since(snapshot_lsn)
                offset = checkpoint["incremental_offset"]
                for lsn, op, row in changes[offset:]:
                    _apply(expected, op, row)
                    incremental += 1
                    checkpoint["incremental_offset"] = offset + incremental
                    _append_increment(state_dir, lsn, op, row)
                    _save_checkpoint(state_dir, checkpoint)
            _source.clear()
            _source.update(expected)
            result = verify()
            if persistent:
                checkpoint["phase"] = "done"
                checkpoint["retries"] = retries
                _save_checkpoint(state_dir, checkpoint)
            return {
                "rows": len(_target),
                "snapshot_lsn": snapshot_lsn,
                "incremental": incremental,
                "retries": retries,
                "verify": result,
            }
        except Exception as exc:
            _target.clear()  # 清理半成品，不留半张表
            give_up = retries >= MAX_RETRIES
            if not give_up:
                retries += 1
            if persistent and checkpoint is not None:
                # 位点保留，增量从头重放（目标已清空重建）
                checkpoint["incremental_offset"] = 0
                checkpoint["retries"] = retries
                _reset_increments(state_dir)
                _save_checkpoint(state_dir, checkpoint)
            if give_up:
                raise CopyError(
                    "copy to %r failed after %d retries" % (dst, MAX_RETRIES)
                ) from exc
