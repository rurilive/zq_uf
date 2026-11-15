"""分区克隆：一致性快照 + 断点续传 + 行数/校验和校验。

- 先取一致性快照（记录版本和行数），快照是只读副本，取完之后源分区的写入
  不影响快照，因此克隆期间源分区继续写入也不会让目标失真；
- 克隆只读源分区，不修改源数据；
- 从快照复制到目标，失败清理半成品，断点（已复制行数）落盘；
- 重试从断点继续，重试次数有上限；
- 复制后对比行数与 SHA-256 校验和，不一致时中止并保留两侧计数；
- 克隆完成后目标分区才对 query() 可见，校验结果写入审计日志。
"""

import copy
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote

MAX_RETRIES = 3
CHECKPOINT_INTERVAL = 16
DEFAULT_STATE_DIR = ".partclone"

_tags = {}
_targets = {}


class CloneError(Exception):
    """克隆失败：目标半成品已清理，断点保留在状态目录，可再次 clone 续传。"""

    def __init__(self, message, checkpoint=None, retries=None, tag=None):
        super().__init__(message)
        self.checkpoint = checkpoint
        self.retries = retries
        self.tag = tag


class VerifyError(CloneError):
    """校验不一致：中止克隆，异常中保留两侧行数与校验和。"""

    def __init__(self, message, result, checkpoint=None, tag=None):
        super().__init__(message, checkpoint=checkpoint, tag=tag)
        self.result = result
        self.rows_source = result["rows_source"]
        self.rows_target = result["rows_target"]
        self.checksum_source = result["checksum_source"]
        self.checksum_target = result["checksum_target"]


class CloneInterrupted(Exception):
    """复制中途中断（测试/演示用来模拟中断）。"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _canonical(row):
    """一行的稳定 JSON 表示，用于计算校验和。"""
    return json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _checksum(rows):
    """按行序对每行的规范 JSON 逐行摘要，得到 SHA-256 校验和。"""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _row_count(rows):
    return len(rows) if rows is not None else 0


def _row_key(row):
    if isinstance(row, dict):
        for key in ("id", "key"):
            if key in row:
                return row[key]
    return None


class Partition:
    """内存分区：可写、可快照；快照是深拷贝，源分区之后继续写不影响快照。

    ``fail_after``/``fail_times`` 是测试与演示用的中断钩子：复制到第
    ``fail_after`` 行后抛 ``CloneInterrupted``，只抛 ``fail_times`` 次；
    ``corrupt_index``/``drop_index`` 用来演示校验不一致（只影响目标副本）。
    """

    def __init__(
        self, tag, rows=None, version=0, fail_after=None, fail_times=1,
        corrupt_index=None, drop_index=None,
    ):
        self.tag = tag
        self.version = int(version)
        self.rows = [copy.deepcopy(row) for row in (rows or [])]
        self.writes = 0
        self.fail_after = fail_after
        self.fail_times = fail_times
        self.corrupt_index = corrupt_index
        self.drop_index = drop_index
        self._interruptions = 0
        # 诊断计数器：克隆复用了落盘快照时可据此确认没有重新取快照
        self.snapshot_calls = 0

    def _bump(self):
        self.version += 1
        self.writes += 1
        return self.version

    def append(self, row):
        """追加一行，版本加一。"""
        self.rows.append(copy.deepcopy(row))
        return self._bump()

    def write(self, row):
        """按 id/key 覆写已有行；没有对应行则追加。版本加一。"""
        key = _row_key(row)
        if key is not None:
            for index, existing in enumerate(self.rows):
                if _row_key(existing) == key:
                    self.rows[index] = copy.deepcopy(row)
                    return self._bump()
        self.rows.append(copy.deepcopy(row))
        return self._bump()

    def delete(self, key):
        """按 id/key 删除一行，版本加一。"""
        for index, existing in enumerate(self.rows):
            if _row_key(existing) == key:
                del self.rows[index]
                return self._bump()
        return self.version

    def snapshot(self):
        """返回一致快照：版本号 + 行数据的深拷贝（只读副本）。"""
        self.snapshot_calls += 1
        return {
            "tag": self.tag,
            "version": self.version,
            "rows": copy.deepcopy(self.rows),
        }

    def after_row(self, index):
        """每复制一行后的钩子；覆盖它以模拟中断。"""
        if (
            self.fail_after is not None
            and index >= self.fail_after
            and self._interruptions < self.fail_times
        ):
            self._interruptions += 1
            raise CloneInterrupted(
                "模拟克隆中断：第 %d 行之后" % (index + 1)
            )

    def row_for_copy(self, row, index):
        """复制前的行变换钩子；返回 None 表示该行丢失。"""
        if self.drop_index is not None and index == self.drop_index:
            return None
        if self.corrupt_index is not None and index == self.corrupt_index:
            corrupted = copy.deepcopy(row)
            if isinstance(corrupted, dict):
                corrupted["_corrupt"] = True
            else:
                return "_corrupt"
            return corrupted
        return row


# ----- 状态目录：快照、断点、审计落盘 -----


def _state_dir(state_dir=None):
    return state_dir or os.environ.get(
        "PARTCLONE_STATE_DIR", DEFAULT_STATE_DIR
    )


def _tag_dir(base, tag):
    name = quote(str(tag), safe="") or "_tag"
    return os.path.join(base, name)


def _atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".partclone-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, default=str)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _snapshot_path(tag_dir):
    return os.path.join(tag_dir, "snapshot.json")


def _checkpoint_path(tag_dir):
    return os.path.join(tag_dir, "checkpoint.json")


def _audit_path(tag_dir):
    return os.path.join(tag_dir, "audit.jsonl")


def _save_snapshot(tag_dir, tag, version, rows, taken_at):
    """快照落盘：版本、行数、校验和与只读行副本。"""
    payload = {
        "tag": tag,
        "version": int(version),
        "row_count": _row_count(rows),
        "checksum": _checksum(rows),
        "rows": rows,
        "taken_at": taken_at,
        "read_only": True,
    }
    _atomic_write_json(_snapshot_path(tag_dir), payload)
    return payload


def _save_checkpoint(tag_dir, tag, snapshot_version, row_count, offset,
                     phase, retries, extra=None):
    """断点落盘：已复制行数（offset）与快照版本，原子写入。"""
    payload = {
        "tag": tag,
        "snapshot_version": int(snapshot_version),
        "row_count": int(row_count),
        "offset": int(offset),
        "phase": phase,
        "retries": int(retries),
        "updated_at": _now(),
    }
    if extra:
        payload.update(extra)
    _atomic_write_json(_checkpoint_path(tag_dir), payload)
    return payload


def _load_resume(tag_dir):
    """读取可续传的快照与断点；不存在、已完成或快照不完整则返回 None。"""
    checkpoint = _read_json(_checkpoint_path(tag_dir))
    snapshot = _read_json(_snapshot_path(tag_dir))
    if not checkpoint or not snapshot:
        return None
    if checkpoint.get("phase") in ("done", "verify_failed"):
        return None
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        return None
    # 快照完整性：落盘的行数、校验和必须与内容一致
    if int(snapshot.get("row_count", -1)) != len(rows):
        return None
    if snapshot.get("checksum") != _checksum(rows):
        return None
    if int(checkpoint.get("row_count", -1)) != len(rows):
        return None
    offset = int(checkpoint.get("offset", 0))
    if offset < 0 or offset > len(rows):
        offset = 0
    return snapshot, offset


def _append_audit(tag_dir, tag, event, **fields):
    """审计追加一行 JSONL；落盘失败不影响克隆主流程。"""
    entry = {"time": _now(), "tag": tag, "event": event}
    entry.update(fields)
    try:
        os.makedirs(tag_dir, exist_ok=True)
        with open(_audit_path(tag_dir), "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(entry, ensure_ascii=False, default=str) + "\n"
            )
    except OSError:
        return entry
    return entry


def audit_log(tag, state_dir=None):
    """读取某标签的审计记录（JSONL 解析后的列表）。"""
    path = _audit_path(_tag_dir(_state_dir(state_dir), tag))
    entries = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except (OSError, ValueError):
        return entries
    return entries


def load_checkpoint(tag, state_dir=None):
    """读取某标签落盘的断点信息；不存在返回 None。"""
    return _read_json(
        _checkpoint_path(_tag_dir(_state_dir(state_dir), tag))
    )


def discard_checkpoint(tag, state_dir=None):
    """删除某标签的快照、断点与审计，下次 clone 会重新取快照。"""
    shutil.rmtree(_tag_dir(_state_dir(state_dir), tag), ignore_errors=True)


# ----- 源快照 -----


def _is_legacy(partition):
    """旧接口：{"tag": ..., "rows": <int>} 只有行数、没有行数据。"""
    if not isinstance(partition, dict):
        return False
    rows = partition.get("rows", 0)
    return isinstance(rows, int) and not isinstance(rows, bool)


def _partition_tag(partition):
    if isinstance(partition, dict):
        return partition.get("tag", "clone")
    return getattr(partition, "tag", "clone")


def _partition_version(partition):
    if isinstance(partition, dict):
        return int(partition.get("version", 0))
    return int(getattr(partition, "version", 0))


def _take_snapshot(partition):
    """取一致性快照，返回 (version, 行数据的深拷贝)。只读，不写源分区。"""
    if hasattr(partition, "snapshot"):
        raw = partition.snapshot()
        if isinstance(raw, dict):
            version = int(raw.get("version", _partition_version(partition)))
            rows = raw.get("rows", [])
        elif isinstance(raw, tuple) and len(raw) == 2:
            version, rows = raw
            version = int(version)
        elif hasattr(raw, "rows"):
            version = int(getattr(raw, "version", 0))
            rows = raw.rows
        else:
            # snapshot() 直接返回行集合
            version = _partition_version(partition)
            rows = raw
    elif isinstance(partition, dict):
        version = _partition_version(partition)
        rows = partition.get("rows", partition.get("data", []))
    elif isinstance(partition, (list, tuple)):
        version = 0
        rows = partition
    else:
        version = _partition_version(partition)
        rows = getattr(partition, "rows", [])

    if isinstance(rows, (str, bytes)) or not hasattr(rows, "__iter__"):
        raise TypeError("分区的 rows 必须是可迭代的行集合")
    # 深拷贝隔离源分区：快照取完后源分区继续写，也不会改变快照
    return version, [copy.deepcopy(row) for row in rows]


# ----- 校验 -----


def _verify_rows(source_rows, target_rows):
    source_checksum = _checksum(source_rows)
    target_checksum = _checksum(target_rows)
    rows_source = _row_count(source_rows)
    rows_target = _row_count(target_rows)
    ok = rows_source == rows_target and source_checksum == target_checksum
    reason = None
    if not ok:
        reason = "rows" if rows_source != rows_target else "checksum"
    return {
        "ok": ok,
        "matched": ok,
        "reason": reason,
        "rows_source": rows_source,
        "rows_target": rows_target,
        "checksum_source": source_checksum,
        "checksum_target": target_checksum,
    }


def _snapshot_record(tag, version, rows, taken_at, resumed_from):
    return {
        "tag": tag,
        "version": int(version),
        "rows": _row_count(rows),
        "row_count": _row_count(rows),
        "checksum": _checksum(rows),
        "read_only": True,
        "taken_at": taken_at,
        "resumed_from": int(resumed_from),
    }


# ----- 克隆 -----


def clone(partition, state_dir=None, max_retries=None, fresh=False):
    """先取一致性快照，再从快照复制到目标分区。

    ``partition`` 可以是 ``Partition``、任何实现 ``snapshot()`` 的对象，
    或带 ``rows`` 行列表的 dict。旧的 ``{"tag", "rows": <int>}`` 只按行数
    复制，保持兼容（snapshot 为 None）。

    返回快照（版本、行数、校验和）、断点、重试次数和校验结果。
    复制失败会清理目标半成品并按落盘断点续传；校验不一致时抛
    ``VerifyError`` 并保留两侧计数。
    """
    tag = _partition_tag(partition)

    # 旧接口：只有行数、没有行数据，保持原有行为
    if _is_legacy(partition):
        rows = max(0, int(partition.get("rows", 0)))
        _tags[tag] = {
            "kind": "legacy",
            "tag": tag,
            "rows": rows,
            "state_dir": _state_dir(state_dir),
        }
        _targets.pop(tag, None)
        return {
            "tag": tag,
            "copied": rows,
            "rows": rows,
            "snapshot": None,
            "resumed": False,
            "resumed_from": 0,
            "retries": 0,
            "checkpoint": None,
            "verify": {
                "ok": True,
                "matched": True,
                "reason": None,
                "rows_source": rows,
                "rows_target": rows,
                "checksum_source": None,
                "checksum_target": None,
            },
            "visible": False,
        }

    max_r = MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    base = _state_dir(state_dir)
    tag_dir = _tag_dir(base, tag)
    if fresh:
        discard_checkpoint(tag, state_dir=base)

    # 克隆完成前目标分区对 query() 不可见
    _targets.pop(tag, None)

    resume = None if fresh else _load_resume(tag_dir)
    if resume is not None:
        # 跨进程续传：复用落盘快照，按断点重建已复制前缀
        snapshot_payload, resumed_from = resume
        version = int(snapshot_payload["version"])
        source_rows = snapshot_payload["rows"]
        taken_at = snapshot_payload.get("taken_at") or _now()
        resumed = True
        _append_audit(
            tag_dir, tag, "resume",
            version=version,
            row_count=_row_count(source_rows),
            offset=resumed_from,
        )
    else:
        # 先取一致快照再复制；快照记录版本和行数
        try:
            version, source_rows = _take_snapshot(partition)
        except Exception as exc:
            _append_audit(
                tag_dir, tag, "give_up",
                phase="snapshot",
                error=type(exc).__name__,
                message=str(exc),
            )
            raise CloneError(
                "分区 %r 取快照失败：%s" % (tag, exc), tag=tag
            ) from exc
        taken_at = _now()
        resumed = False
        resumed_from = 0
        _save_snapshot(tag_dir, tag, version, source_rows, taken_at)

    row_count = _row_count(source_rows)
    snapshot = _snapshot_record(
        tag, version, source_rows, taken_at, resumed_from
    )
    _tags[tag] = {
        "kind": "rich",
        "tag": tag,
        "version": version,
        "rows": row_count,
        "checksum": snapshot["checksum"],
        "snapshot": snapshot,
        "state_dir": base,
    }

    # 已复制前缀来自快照（跨进程续传时按断点重建），本次进程内继续追加
    target = [copy.deepcopy(row) for row in source_rows[:resumed_from]]
    offset = resumed_from
    retries = 0
    checkpoint = _save_checkpoint(
        tag_dir, tag, version, row_count, offset, "copying", retries
    )
    _append_audit(
        tag_dir, tag, "attempt",
        attempt=1,
        retries=retries,
        offset=offset,
        row_count=row_count,
        snapshot_version=version,
        resumed=resumed,
    )

    def _discard():
        """失败清理：丢弃半成品，目标保持不可见。"""
        _targets.pop(tag, None)

    while True:
        try:
            for index in range(offset, row_count):
                source_row = source_rows[index]
                copy_row = source_row
                if hasattr(partition, "row_for_copy"):
                    copy_row = partition.row_for_copy(
                        copy.deepcopy(source_row), index
                    )
                if copy_row is not None:
                    target.append(copy.deepcopy(copy_row))
                offset = index + 1
                # 按间隔落断点（末行必落），进程中断后可从最后确认行继续
                if offset % CHECKPOINT_INTERVAL == 0 or offset == row_count:
                    checkpoint = _save_checkpoint(
                        tag_dir, tag, version, row_count, offset,
                        "copying", retries,
                    )
                if hasattr(partition, "after_row"):
                    partition.after_row(index)

            result = _verify_rows(source_rows, target)
            if not result["ok"]:
                # 校验不一致：中止，保留两侧计数，目标不可见
                checkpoint = _save_checkpoint(
                    tag_dir, tag, version, row_count, offset,
                    "verify_failed", retries,
                )
                _discard()
                # 保留失败时的两侧计数，供事后 verify(tag) 查看
                _tags[tag]["last_verify"] = result
                _append_audit(
                    tag_dir, tag, "verify_failed",
                    rows_source=result["rows_source"],
                    rows_target=result["rows_target"],
                    checksum_source=result["checksum_source"],
                    checksum_target=result["checksum_target"],
                    reason=result["reason"],
                )
                raise VerifyError(
                    "分区 %r 校验不一致：行数 %d/%d，校验和 %s/%s"
                    % (
                        tag,
                        result["rows_source"],
                        result["rows_target"],
                        result["checksum_source"],
                        result["checksum_target"],
                    ),
                    result,
                    checkpoint=checkpoint,
                    tag=tag,
                )

            # 成功：目标分区对查询可见
            _targets[tag] = target
            checkpoint = _save_checkpoint(
                tag_dir, tag, version, row_count, row_count,
                "done", retries,
            )
            _append_audit(
                tag_dir, tag, "success",
                retries=retries,
                resumed=resumed,
                resumed_from=resumed_from,
                rows=result["rows_target"],
                checksum=result["checksum_target"],
                verify=result["ok"],
            )
            return {
                "tag": tag,
                "copied": result["rows_target"],
                "rows": result["rows_target"],
                "snapshot": snapshot,
                "resumed": resumed,
                "resumed_from": resumed_from,
                "retries": retries,
                "checkpoint": checkpoint,
                "verify": result,
                "visible": True,
            }
        except VerifyError:
            raise
        except Exception as exc:
            # 失败：清理目标半成品（保持不可见），断点保留用于续传
            _discard()
            checkpoint = _save_checkpoint(
                tag_dir, tag, version, row_count, offset,
                "copying", retries,
            )
            _append_audit(
                tag_dir, tag, "cleanup",
                offset=offset,
                retries=retries,
                error=type(exc).__name__,
                message=str(exc),
            )
            if retries >= max_r:
                _append_audit(
                    tag_dir, tag, "give_up",
                    offset=offset,
                    retries=retries,
                    error=type(exc).__name__,
                )
                raise CloneError(
                    "克隆 %r 失败，已重试 %d 次；目标已清理，断点保留在 %s"
                    % (tag, retries, tag_dir),
                    checkpoint=checkpoint,
                    retries=retries,
                    tag=tag,
                ) from exc
            retries += 1
            if offset > 0 and not resumed:
                # 本次调用内已复制前缀保留，从断点续传
                resumed = True
                resumed_from = offset
            _append_audit(
                tag_dir, tag, "retry",
                attempt=retries + 1,
                retries=retries,
                offset=offset,
            )


def verify(tag):
    """对比快照基线与目标分区：行数 + 校验和。

    返回两侧行数与校验和；不一致时 ``matched``/``ok`` 为 False。
    旧接口克隆（只有行数）保持原行为：matched 为 True、checksum 为 None。
    """
    meta = _tags.get(tag)
    if meta is None:
        return {
            "tag": tag,
            "rows": 0,
            "matched": False,
            "ok": False,
            "reason": "unknown",
            "row_visible": False,
            "rows_source": 0,
            "rows_target": 0,
            "checksum_source": None,
            "checksum_target": None,
            "checksum": None,
        }

    if meta.get("kind") == "legacy":
        rows = int(meta["rows"])
        return {
            "tag": tag,
            "rows": rows,
            "matched": True,
            "ok": True,
            "reason": None,
            "row_visible": False,
            "rows_source": rows,
            "rows_target": rows,
            "checksum_source": None,
            "checksum_target": None,
            "checksum": None,
        }

    target = _targets.get(tag)
    last_verify = meta.get("last_verify")
    target_rows = target if target is not None else []
    source_checksum = meta["checksum"]
    target_checksum = _checksum(target_rows)
    ok = (
        target is not None
        and int(meta["rows"]) == _row_count(target_rows)
        and source_checksum == target_checksum
    )
    reason = None
    rows_target = _row_count(target_rows)
    if not ok:
        if target is None:
            reason = "invisible"
        elif int(meta["rows"]) != _row_count(target_rows):
            reason = "rows"
        else:
            reason = "checksum"
        if target is None and last_verify:
            # 校验失败时中止：仍保留失败那一刻的两侧计数与校验和
            reason = last_verify["reason"]
            rows_target = last_verify["rows_target"]
            target_checksum = last_verify["checksum_target"]

    result = {
        "tag": tag,
        "ok": ok,
        "matched": ok,
        "reason": reason,
        "row_visible": target is not None,
        "rows": rows_target,
        "rows_source": int(meta["rows"]),
        "rows_target": rows_target,
        "checksum_source": source_checksum,
        "checksum_target": target_checksum,
        "checksum": target_checksum,
    }

    # 校验结果写审计：成功与不一致都记录两侧计数与校验和
    _append_audit(
        _tag_dir(meta.get("state_dir", _state_dir()), tag),
        tag, "verify",
        ok=ok,
        reason=reason,
        rows_source=result["rows_source"],
        rows_target=result["rows_target"],
        checksum_source=source_checksum,
        checksum_target=target_checksum,
    )
    return result


# ----- 查询与重置 -----


def query(tag):
    """查询目标分区；只有克隆完成后才可见，克隆期间返回 None。"""
    if tag not in _targets:
        return None
    return copy.deepcopy(_targets[tag])


def is_visible(tag):
    """目标分区是否已克隆完成、对查询可见。"""
    return tag in _targets


def reset():
    """清空内存中的克隆状态（不删状态目录，用于测试续传）。"""
    _tags.clear()
    _targets.clear()
