"""快照与变更流的位点对齐。

- snapshot 记录起始位点，快照窗口内到达的变更先缓存，提交时按序补应用。
- 位点单调推进，pos 不大于最后确认位点的事件直接跳过，重放幂等。
- 快照和变更流共用同一个位点文件，恢复后从最后确认位点继续。
- 一致性校验对比行数与 SHA-256 校验和，失败时给出差异行。
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

DEFAULT_POSITION_FILE = "cdcsnapshot_position.json"


def _checksum(rows):
    """按 key 排序后对每行做稳定 JSON 序列化，生成 SHA-256 校验和。"""
    digest = hashlib.sha256()
    for key in sorted(rows):
        payload = json.dumps(
            [key, rows[key]],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _diff_rows(expected, actual):
    """对比两份行数据，返回每个差异行的期望值与实际值。"""
    diff = []
    for key in sorted(set(expected) | set(actual)):
        in_expected = key in expected
        in_actual = key in actual
        if not (in_expected and in_actual) or expected[key] != actual[key]:
            diff.append(
                {
                    "key": key,
                    "expected": expected[key] if in_expected else None,
                    "actual": actual[key] if in_actual else None,
                }
            )
    return diff


class SnapshotStore:
    """承载行数据、确认位点、快照缓存和共用位点文件。"""

    def __init__(self, position_file=DEFAULT_POSITION_FILE, rows=None):
        self.position_file = position_file
        self.rows = {} if rows is None else rows
        self.position = 0
        self.pending = []
        self.snapshot_info = None
        self._load_position()

    # ----- 位点文件（快照与变更流共用） -----
    def _load_position(self):
        self.position = 0
        if os.path.exists(self.position_file):
            with open(self.position_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.position = int(state.get("position", 0))
        self._last_saved_position = self.position
        self._last_saved_marker = None

    def _save_position(self, table=None, snapshot=None, force=False):
        file_exists = os.path.exists(self.position_file)
        # 尚无位点文件且位点为 0 时不落盘，避免空快照/无位点操作产生文件
        if not file_exists and self.position == 0:
            return
        # 位点未推进且快照标记无变化时不落盘，force 用于快照窗口开关
        if (
            not force
            and self.position == self._last_saved_position
            and snapshot == self._last_saved_marker
            and file_exists
        ):
            return
        state = {
            "position": self.position,
            "table": table,
            "snapshot": snapshot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        directory = os.path.dirname(os.path.abspath(self.position_file))
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".cdcpos-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.position_file)
        except BaseException:
            os.unlink(tmp_path)
            raise
        self._last_saved_position = self.position
        self._last_saved_marker = snapshot

    # ----- 变更应用 -----
    def _apply_one(self, event):
        key = event["key"]
        if event.get("op", "upsert") == "delete":
            self.rows.pop(key, None)
        else:
            self.rows[key] = event["row"]

    def _is_replay(self, event):
        pos = event.get("pos")
        return pos is not None and pos <= self.position

    def apply_events(self, events):
        """应用一批变更；快照窗口内只缓存，重放事件直接跳过。"""
        applied = cached = skipped = 0
        advanced = False
        for event in events:
            if self._is_replay(event):
                skipped += 1
                continue
            if self.snapshot_info is not None:
                self.pending.append(event)
                cached += 1
                continue
            self._apply_one(event)
            applied += 1
            pos = event.get("pos")
            if pos is not None and pos > self.position:
                self.position = pos
                advanced = True
        if advanced:
            self._save_position()
        return {
            "applied": applied,
            "cached": cached,
            "skipped": skipped,
            "position": self.position,
        }

    # ----- 快照窗口 -----
    def begin_snapshot(self, table):
        """记录快照起始位点；窗口内的变更进入缓存。"""
        if self.snapshot_info is not None:
            raise RuntimeError("快照进行中，请先调用 commit_snapshot()")
        self.snapshot_info = {
            "table": table,
            "start_position": self.position,
        }
        self._save_position(
            table=table,
            snapshot={"table": table, "start_position": self.position},
            force=True,
        )
        return self.position

    def commit_snapshot(self):
        """补应用缓存的变更，推进位点，返回带校验结果的快照。"""
        if self.snapshot_info is None:
            raise RuntimeError("没有进行中的快照，请先调用 begin_snapshot()")
        info = self.snapshot_info
        applied = skipped = 0
        advanced = False
        for event in self.pending:
            if self._is_replay(event):
                skipped += 1
                continue
            self._apply_one(event)
            applied += 1
            pos = event.get("pos")
            if pos is not None and pos > self.position:
                self.position = pos
                advanced = True
        self.pending = []
        self.snapshot_info = None
        self._save_position(table=info["table"], force=True)
        return self._build_result(
            info["table"], info["start_position"], applied, skipped
        )

    def snapshot(self, table):
        """无并发写入时的快捷封装：begin + commit。"""
        self.begin_snapshot(table)
        return self.commit_snapshot()

    def _build_result(self, table, start_position, cached, skipped):
        return {
            "table": table,
            "rows": len(self.rows),
            "data": dict(self.rows),
            "start_position": start_position,
            "position": self.position,
            "cached": cached,
            "skipped": skipped,
            "check": {
                "rows": len(self.rows),
                "checksum": _checksum(self.rows),
                "ok": True,
                "diff": [],
            },
        }

    # ----- 一致性校验 -----
    def check_consistency(self, table, expected):
        """对比行数和校验和；不一致时在 diff 中给出差异行。"""
        expected_rows = expected["rows"]
        expected_checksum = expected.get("checksum")
        if expected_checksum is None:
            expected_checksum = expected.get("check", {}).get("checksum")
        expected_data = expected.get("data", {})
        actual_checksum = _checksum(self.rows)
        ok = (
            len(self.rows) == expected_rows
            and actual_checksum == expected_checksum
        )
        return {
            "table": table,
            "ok": ok,
            "rows": len(self.rows),
            "checksum": actual_checksum,
            "expected_rows": expected_rows,
            "expected_checksum": expected_checksum,
            "diff": [] if ok else _diff_rows(expected_data, self.rows),
        }


# 模块级默认存储；_rows 保持为可变 dict，兼容 module._rows.clear()。
_store = SnapshotStore()
_rows = _store.rows


def apply(events):
    """应用变更事件，返回 applied/cached/skipped/position。"""
    return _store.apply_events(events)


def snapshot(table):
    """全量快照并输位点、缓存数量和校验结果。"""
    return _store.snapshot(table)


def begin_snapshot(table):
    """开启快照窗口，返回快照起始位点。"""
    return _store.begin_snapshot(table)


def commit_snapshot():
    """提交快照：补应用缓存变更并推进位点。"""
    return _store.commit_snapshot()


def check_consistency(table, expected):
    """按行数与校验和校验一致性，失败时携带差异行。"""
    return _store.check_consistency(table, expected)


def checksum():
    """当前行数据的 SHA-256 校验和。"""
    return _checksum(_store.rows)


def reset(position_file=DEFAULT_POSITION_FILE):
    """清空内存状态并按位点文件重新加载（测试/演示用）。"""
    global _store
    _rows.clear()
    _store = SnapshotStore(position_file=position_file, rows=_rows)
    return _store
