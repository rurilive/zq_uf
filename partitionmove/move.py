"""分区迁移：双写、位点、原子切换、校验和回滚。

阶段：prepare -> copy -> catchup -> validate -> switch -> done
失败：validate / switch 失败时自动 rollback，旧路径始终保留并校验。

持久化（state_dir）：
    manifest.json          切换清单（原子替换）
    data.json              各路径上的分区行数据（旧路径不删除）
    journal/<hash>.json    迁移位点与阶段
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

DEFAULT_SOURCE = "hot"
MANIFEST_NAME = "manifest.json"
DATA_NAME = "data.json"
JOURNAL_DIR = "journal"

_lock = threading.RLock()

_JSON_SEP = (",", ":")


class MigrationError(Exception):
    """迁移过程中的可控错误。"""


class ValidationError(MigrationError):
    """切换前行数/校验和不一致。"""

    def __init__(self, message, validation):
        super().__init__(message)
        self.validation = validation


# ---------------------------------------------------------------------------
# 校验与持久化
# ---------------------------------------------------------------------------


def _checksum(rows):
    payload = json.dumps(rows, sort_keys=True, separators=_JSON_SEP, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_json(value, label):
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        raise TypeError(f"{label} 必须可 JSON 序列化") from exc


def _fsync_dir(directory):
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _journal_key(partition):
    return hashlib.sha256(partition.encode("utf-8")).hexdigest()[:16]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 落盘状态
# ---------------------------------------------------------------------------


class _State:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.moved = {}
        self.manifest = {"version": 1, "partitions": {}}
        self.store = {"paths": {}}
        self.journals = {}
        self._load()

    # -- 读取 ---------------------------------------------------------------
    def _load(self):
        manifest_file = self.dir / MANIFEST_NAME
        if manifest_file.exists():
            self.manifest = _read_json(manifest_file)
            self.manifest.setdefault("partitions", {})
        data_file = self.dir / DATA_NAME
        if data_file.exists():
            self.store = _read_json(data_file)
            self.store.setdefault("paths", {})

        journal_dir = self.dir / JOURNAL_DIR
        if journal_dir.exists():
            for path in sorted(journal_dir.glob("*.json")):
                try:
                    journal = _read_json(path)
                except (OSError, ValueError):
                    continue
                if journal.get("phase") not in (None, "done", "rolled_back"):
                    self.journals[journal["partition"]] = journal

        # 清单是活跃路径的来源；moved 是它在当前进程中的视图（旧测试会清空）。
        for partition, entry in self.manifest["partitions"].items():
            active = entry.get("active_path", entry.get("target"))
            if active and active != DEFAULT_SOURCE:
                self.moved[partition] = active
            else:
                self.moved.pop(partition, None)

    def switched_partitions(self):
        return {
            partition
            for partition, entry in self.manifest["partitions"].items()
            if entry.get("state") == "switched"
        }

    # -- 写入 ---------------------------------------------------------------
    def save_data(self):
        _atomic_write_json(self.dir / DATA_NAME, self.store)

    def journal_path(self, partition):
        return self.dir / JOURNAL_DIR / f"{_journal_key(partition)}.json"

    def save_journal(self, partition):
        _atomic_write_json(self.journal_path(partition), self.journals[partition])

    def drop_journal(self, partition):
        self.journals.pop(partition, None)
        try:
            self.journal_path(partition).unlink()
        except FileNotFoundError:
            pass

    def rows_ref(self, path, partition):
        return self.store["paths"].setdefault(path, {}).setdefault(partition, [])

    def rows_at(self, path, partition):
        return copy.deepcopy(self.store["paths"].get(path, {}).get(partition, []))


def _default_state_dir():
    return os.environ.get("PARTITIONMOVE_STATE_DIR") or tempfile.mkdtemp(
        prefix="partitionmove-"
    )


_states = {}
_DEFAULT_DIR = os.path.abspath(_default_state_dir())


def _get_state(state_dir=None):
    key = os.path.abspath(state_dir) if state_dir else _DEFAULT_DIR
    state = _states.get(key)
    if state is None:
        state = _State(key)
        _states[key] = state
    return state


_default_state = _get_state(_DEFAULT_DIR)
# 兼容旧接口/旧测试：_moved 保留为“当前进程内的活跃迁移”字典。
_moved = _default_state.moved


def _active_path(state, partition):
    return state.moved.get(partition, DEFAULT_SOURCE)


def _verify_prefix(rows, baseline_rows, baseline_checksum):
    """旧路径只允许追加：迁移期间双写可能追加行，原有行必须原样保留。"""
    prefix = rows[:baseline_rows]
    prefix_checksum = _checksum(prefix)
    rows_ok = len(rows) >= baseline_rows
    checksum_ok = prefix_checksum == baseline_checksum
    return {
        "rows": len(rows),
        "baseline_rows": baseline_rows,
        "checksum": prefix_checksum,
        "expected_checksum": baseline_checksum,
        "ok": rows_ok and checksum_ok,
        "checks": {"rows": rows_ok, "checksum": checksum_ok},
    }


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------


def _call_hook(hooks, name, info):
    fn = (hooks or {}).get(name)
    if fn is not None:
        fn(dict(info))


def _set_journal(state, partition, phase, **updates):
    journal = state.journals[partition]
    journal["phase"] = phase
    journal["updated_at"] = _now()
    journal.update(updates)
    state.save_journal(partition)
    return journal


def _snapshot_journal(state, partition, journal):
    source = journal["source"]
    target = journal["target"]
    source_rows = state.rows_ref(source, partition)
    target_rows = state.rows_ref(target, partition)
    journal["source_rows"] = len(source_rows)
    journal["source_checksum"] = _checksum(source_rows)
    journal["target_rows"] = len(target_rows)
    journal["target_checksum"] = _checksum(target_rows)
    journal["cursor"] = len(target_rows)
    state.save_journal(partition)
    return journal


def _validation_of(journal, source_rows, target_rows):
    source_checksum = _checksum(source_rows)
    target_checksum = _checksum(target_rows)
    return {
        "rows": len(source_rows),
        "target_rows": len(target_rows),
        "checksum": source_checksum,
        "target_checksum": target_checksum,
        "ok": len(source_rows) == len(target_rows)
        and source_checksum == target_checksum,
        "checks": {
            "rows": len(source_rows) == len(target_rows),
            "checksum": source_checksum == target_checksum,
        },
    }


def _write_manifest(state, entry):
    """原子写清单：tmp + fsync + os.replace。读者只会看到完整旧/新清单。"""
    manifest = copy.deepcopy(state.manifest)
    manifest.setdefault("partitions", {})[entry["partition"]] = entry
    _atomic_write_json(state.dir / MANIFEST_NAME, manifest)
    state.manifest = manifest
    if entry.get("active_path") != DEFAULT_SOURCE:
        state.moved[entry["partition"]] = entry["active_path"]
    else:
        state.moved.pop(entry["partition"], None)


def _rollback_entry(partition, journal, entry=None):
    """生成回滚后的清单条目：记录旧路径及其校验口径。"""
    base = dict(entry or {})
    base.update(
        partition=partition,
        migration_id=journal.get("migration_id", base.get("migration_id")),
        source=journal["source"],
        target=journal["target"],
        active_path=journal["source"],
        state="rolled_back",
        source_rows=journal["source_rows"],
        source_checksum=journal["source_checksum"],
        rolled_back_at=_now(),
    )
    return base


def _restore_source(state, partition, source, target, source_rows, source_checksum,
                    replay_tail=False):
    """校验旧路径并（可选）把切换后写入新路径的尾部补回旧路径。

    旧路径只允许追加：迁移期间双写可能追加行，原有行必须原样保留。
    """
    rows = state.rows_ref(source, partition)
    verification = _verify_prefix(rows, source_rows, source_checksum)
    replayed = 0
    if replay_tail and verification["ok"]:
        # 切换后新写入只落在 target；回滚时把 target 中旧路径还没有的尾部补回，
        # 否则这些已确认的写入会随切换回退丢失。
        target_rows = state.rows_at(target, partition)
        tail = target_rows[len(rows):]
        if tail:
            rows.extend(copy.deepcopy(tail))
            replayed = len(tail)
            verification = _verify_prefix(rows, source_rows, source_checksum)
    verification["replayed"] = replayed
    return verification


def _auto_rollback(state, partition, journal, error, phases):
    """失败回滚：旧路径不删除，按 journal 快照校验并更新清单。"""
    entry = state.manifest.get("partitions", {}).get(partition)
    if entry is not None and entry.get("migration_id") != journal.get("migration_id"):
        entry = None
    was_switched = bool(entry and entry.get("state") == "switched")
    _write_manifest(state, _rollback_entry(partition, journal, entry))

    source = journal["source"]
    restored = _restore_source(
        state,
        partition,
        source,
        journal["target"],
        journal["source_rows"],
        journal["source_checksum"],
        replay_tail=was_switched,
    )
    state.drop_journal(partition)
    state.save_data()
    phases.append("rollback")
    return {
        "restored": source,
        "verified": restored["ok"],
        "verification": restored,
        "manifest_reverted": was_switched,
        "error": str(error),
    }


def move(partition, target, *, state_dir=None, hooks=None):
    """把 partition 从当前活跃路径迁移到 target。

    迁移期间通过 write() 写入会双写并推进位点；追上后校验行数和校验和，
    再原子切换清单。校验或切换失败会在返回前自动回滚，旧路径保留。
    """
    if not isinstance(partition, str) or not isinstance(target, str):
        raise TypeError("partition 和 target 必须是字符串")
    if not partition or not target:
        raise ValueError("partition 和 target 不能为空")

    with _lock:
        state = _get_state(state_dir)
        source = _active_path(state, partition)
        if source == target:
            raise ValueError(f"{partition} 已经位于 {target}")

        stale = state.journals.get(partition)
        if stale is not None:
            raise MigrationError(
                f"{partition} 存在未完成的迁移（{stale.get('phase')}），请先 rollback()"
            )

        migration_id = uuid.uuid4().hex
        source_rows = state.rows_at(source, partition)
        journal = {
            "partition": partition,
            "migration_id": migration_id,
            "source": source,
            "target": target,
            "phase": "prepare",
            "cursor": 0,
            "source_rows": len(source_rows),
            "source_checksum": _checksum(source_rows),
            "target_rows": 0,
            "target_checksum": _checksum([]),
            "started_at": _now(),
            "updated_at": _now(),
        }
        state.journals[partition] = journal
        state.save_journal(partition)
        phases = ["prepare"]
        validation = None
        cursor = 0

        try:
            # copy：目标分区数据被源快照替换；位点 = 已复制行数。
            _set_journal(state, partition, "copy")
            target_ref = state.rows_ref(target, partition)
            target_ref[:] = copy.deepcopy(source_rows)
            state.save_data()
            journal = _snapshot_journal(state, partition, journal)
            cursor = journal["cursor"]
            phases.append("copy")

            # catchup：允许钩子在迁移期写入（走双写），随后按位点补齐。
            _set_journal(state, partition, "catchup")
            _call_hook(
                hooks,
                "before_switch",
                {
                    "partition": partition,
                    "source": source,
                    "target": target,
                    "phase": "catchup",
                    "cursor": journal["cursor"],
                    "state_dir": str(state.dir),
                },
            )
            source_ref = state.rows_ref(source, partition)
            target_ref = state.rows_ref(target, partition)
            while len(target_ref) < len(source_ref):
                target_ref.append(copy.deepcopy(source_ref[len(target_ref)]))
            journal = _snapshot_journal(state, partition, journal)
            cursor = journal["cursor"]
            phases.append("catchup")

            # validate：切换前校验行数与校验和。
            _set_journal(state, partition, "validate")
            validation = _validation_of(
                journal,
                state.rows_ref(source, partition),
                state.rows_ref(target, partition),
            )
            if not validation["ok"]:
                raise ValidationError("切换前校验失败", validation)
            phases.append("validate")

            # switch：钩子可注入故障；写入后再次校验，再原子替换清单。
            _set_journal(state, partition, "switch")
            phases.append("switch")
            _call_hook(
                hooks,
                "in_switch",
                {
                    "partition": partition,
                    "source": source,
                    "target": target,
                    "phase": "switch",
                    "cursor": journal["cursor"],
                    "state_dir": str(state.dir),
                },
            )
            source_ref = state.rows_ref(source, partition)
            target_ref = state.rows_ref(target, partition)
            validation = _validation_of(journal, source_ref, target_ref)
            if not validation["ok"]:
                raise ValidationError("切换时校验失败", validation)
            journal = _snapshot_journal(state, partition, journal)
            cursor = journal["cursor"]

            entry = {
                "partition": partition,
                "migration_id": migration_id,
                "source": source,
                "target": target,
                "active_path": target,
                "state": "switched",
                "rows": validation["rows"],
                "checksum": validation["checksum"],
                "source_rows": journal["source_rows"],
                "source_checksum": journal["source_checksum"],
                "target_rows": journal["target_rows"],
                "target_checksum": journal["target_checksum"],
                "switched_at": _now(),
            }
            _write_manifest(state, entry)
            _call_hook(
                hooks,
                "after_switch",
                {
                    "partition": partition,
                    "source": source,
                    "target": target,
                    "phase": "switch",
                    "cursor": journal["cursor"],
                    "state_dir": str(state.dir),
                },
            )
            _set_journal(state, partition, "done")
            state.drop_journal(partition)
            state.save_data()
            phases.append("done")

            return {
                "partition": partition,
                "source": source,
                "target": target,
                "moved": 1,
                "ok": True,
                "phase": "done",
                "phases": phases,
                "cursor": cursor,
                "validation": validation,
                "switch": {
                    "ok": True,
                    "active_path": target,
                    "manifest": str(state.dir / MANIFEST_NAME),
                },
                "rolled_back": False,
                "state_dir": str(state.dir),
                "journal": str(state.journal_path(partition)),
            }

        except Exception as exc:
            validation = validation or {
                "ok": False,
                "rows": None,
                "checks": {"rows": None, "checksum": None},
            }
            rollback = _auto_rollback(state, partition, journal, exc, phases)
            return {
                "partition": partition,
                "source": source,
                "target": target,
                "moved": 0,
                "ok": False,
                "phase": "rollback",
                "phases": phases,
                "cursor": cursor,
                "validation": validation,
                "switch": {
                    "ok": False,
                    "attempted": "switch" in phases,
                    "error": str(exc),
                    "active_path": rollback["restored"],
                },
                "rolled_back": True,
                "rollback": rollback,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "state_dir": str(state.dir),
                "journal": str(state.journal_path(partition)),
            }


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------


def rollback(*, state_dir=None, partition=None):
    """按切换清单恢复旧路径并校验。

    指定 partition 时按清单/journal 回滚该分区；不指定时回滚当前会话可见的
    全部迁移。没有可回滚对象时保持旧接口行为，抛出 NotImplementedError。
    """
    with _lock:
        state = _get_state(state_dir)
        switched = state.switched_partitions()
        if partition is not None:
            if partition in switched or partition in state.journals:
                candidates = [partition]
            else:
                candidates = []
            if not candidates:
                raise NotImplementedError(f"{partition} 没有可回滚的迁移")
        else:
            # 无参回滚沿用旧语义：只回滚当前会话可见的迁移（moved/journal），
            # 没有可回滚对象时保持旧的 NotImplementedError。
            candidates = sorted(set(state.moved) | set(state.journals))
            if not candidates:
                raise NotImplementedError("没有可回滚的迁移")

        results = []
        ok = True
        for name in candidates:
            entry = state.manifest.get("partitions", {}).get(name)
            journal = state.journals.get(name)
            if journal is not None and (
                entry is None or entry.get("migration_id") != journal.get("migration_id")
            ):
                # 未切换的迁移：旧路径从未删除，校验 prepare 快照即可。
                verification = _verify_prefix(
                    state.rows_at(journal["source"], name),
                    journal["source_rows"],
                    journal["source_checksum"],
                )
                state.drop_journal(name)
                _write_manifest(state, _rollback_entry(name, journal))
                state.save_data()
                result = {
                    "partition": name,
                    "restored": journal["source"],
                    "verified": verification["ok"],
                    "verification": verification,
                    "source": journal["source"],
                    "target": journal["target"],
                    "from": "journal",
                }
                results.append(result)
                ok = ok and verification["ok"]
                continue

            if entry is None:
                raise NotImplementedError(f"{name} 没有可回滚的迁移")

            source = entry["source"]
            was_switched = entry.get("state") == "switched"
            verification = _restore_source(
                state,
                name,
                source,
                entry["target"],
                entry["source_rows"],
                entry["source_checksum"],
                replay_tail=was_switched,
            )
            if verification["ok"]:
                _write_manifest(state, _rollback_entry(name, entry))
                state.save_data()
            results.append(
                {
                    "partition": name,
                    "restored": source,
                    "verified": verification["ok"],
                    "verification": verification,
                    "source": source,
                    "target": entry["target"],
                    "from": "manifest",
                }
            )
            ok = ok and verification["ok"]

        return {
            "ok": ok,
            "count": len(results),
            "rolled_back": results,
            "phases": ["rollback", "verify"],
            "state_dir": str(state.dir),
            "manifest": str(state.dir / MANIFEST_NAME),
        }


# ---------------------------------------------------------------------------
# 数据访问：双写、读取、测试/演示辅助
# ---------------------------------------------------------------------------


def write(partition, row, *, state_dir=None):
    """写一行数据；迁移期间双写旧路径和新路径，并推进位点。"""
    _ensure_json(row, "row")
    with _lock:
        state = _get_state(state_dir)
        active = _active_path(state, partition)
        state.rows_ref(active, partition).append(copy.deepcopy(row))
        journal = state.journals.get(partition)
        double_written = False
        cursor = None
        if journal is not None and journal.get("phase") not in (None, "done", "rolled_back"):
            source = journal["source"]
            target = journal["target"]
            if active == source:
                state.rows_ref(target, partition).append(copy.deepcopy(row))
                double_written = True
                cursor = len(state.rows_ref(source, partition))
            else:
                cursor = len(state.rows_ref(target, partition))
            journal["cursor"] = cursor
            journal["updated_at"] = _now()
            state.save_journal(partition)
        state.save_data()
        return {
            "partition": partition,
            "path": active,
            "double_written": double_written,
            "cursor": cursor,
            "rows": len(state.rows_ref(active, partition)),
        }


def read(partition, *, state_dir=None):
    """读当前活跃路径上的分区数据。"""
    with _lock:
        state = _get_state(state_dir)
        return state.rows_at(_active_path(state, partition), partition)


def rows_at(partition, path, *, state_dir=None):
    """读指定路径上的分区数据（旧路径数据在切换后仍保留）。"""
    with _lock:
        return _get_state(state_dir).rows_at(path, partition)


def seed(partition, rows, *, path=None, state_dir=None):
    """测试/演示用：直接写入某路径的分区数据。"""
    _ensure_json(rows, "rows")
    with _lock:
        state = _get_state(state_dir)
        resolved = path or _active_path(state, partition)
        state.store["paths"].setdefault(resolved, {})[partition] = copy.deepcopy(rows)
        state.save_data()
        return {"partition": partition, "path": resolved, "rows": len(rows)}


def progress(partition, *, state_dir=None):
    """从磁盘读取 journal（位点/阶段），不存在则返回 None。"""
    with _lock:
        state = _get_state(state_dir)
        path = state.journal_path(partition)
        if not path.exists():
            return None
        journal = _read_json(path)
        journal["journal"] = str(path)
        return journal


def artifacts(*, state_dir=None):
    """状态文件路径，便于演示/排查。"""
    with _lock:
        state = _get_state(state_dir)
        return {
            "state_dir": str(state.dir),
            "manifest": str(state.dir / MANIFEST_NAME),
            "data": str(state.dir / DATA_NAME),
            "journals": str(state.dir / JOURNAL_DIR),
        }
