"""表结构双向迁移：逐步校验、失败保留现场、支持回退。"""

import hashlib
import json
import os
import time
from datetime import datetime

MIN_VERSION = 1

_version = {"value": MIN_VERSION}
_rows = []
_log = []


class MigrationError(Exception):
    """单步迁移校验失败，现场保留在该步。"""


class Step:
    """一步迁移，声明向前和向后变换；version 是 forward 完成后的版本。"""

    def __init__(self, version, description, forward, backward, expect_rows_delta=0):
        self.version = version
        self.description = description
        self.forward = forward
        self.backward = backward
        self.expect_rows_delta = expect_rows_delta


def _add_email(rows):
    for row in rows:
        row.setdefault("email", row.get("contact", ""))


def _drop_email(rows):
    for row in rows:
        row.pop("email", None)


def _split_name(rows):
    for row in rows:
        first, _, last = row.get("name", "").partition(" ")
        row.setdefault("first_name", first)
        row.setdefault("last_name", last)


def _join_name(rows):
    for row in rows:
        row.pop("first_name", None)
        row.pop("last_name", None)


STEPS = [
    Step(2, "add email, dual-write with contact", _add_email, _drop_email),
    Step(3, "split name into first/last, dual-write with name", _split_name, _join_name),
]


def current():
    return _version["value"]


def reset():
    _version["value"] = MIN_VERSION
    _rows.clear()
    _log.clear()


def _checksum(rows):
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def insert(row):
    """写入一行；字段并存期对新旧字段做双写。"""
    row = dict(row)
    version = _version["value"]
    if version >= 2:
        if "contact" in row:
            row.setdefault("email", row["contact"])
        if "email" in row:
            row.setdefault("contact", row["email"])
    if version >= 3:
        if "name" in row:
            first, _, last = row["name"].partition(" ")
            row.setdefault("first_name", first)
            row.setdefault("last_name", last)
        if "first_name" in row or "last_name" in row:
            row.setdefault(
                "name",
                (row.get("first_name", "") + " " + row.get("last_name", "")).strip(),
            )
    _rows.append(row)
    return row


def read_field(row, field):
    """读时兼容：新字段缺失时回退到旧字段，反之亦然。"""
    if field in row:
        return row[field]
    if field == "email":
        return row.get("contact")
    if field == "contact":
        return row.get("email")
    if field in ("first_name", "last_name") and "name" in row:
        first, _, last = row["name"].partition(" ")
        return first if field == "first_name" else (last or None)
    if field == "name" and ("first_name" in row or "last_name" in row):
        return (row.get("first_name", "") + " " + row.get("last_name", "")).strip()
    return row.get(field)


def rows():
    return [dict(row) for row in _rows]


def migration_log():
    return [dict(record) for record in _log]


def _find_step(version):
    for step in STEPS:
        if step.version == version:
            return step
    return None


def _run(direction, target, operator):
    start_version = _version["value"]
    operator = operator or os.environ.get("USER") or "unknown"
    started = time.monotonic()
    steps_done = []
    ok = True
    error = None
    failed_step = None
    while _version["value"] != target:
        from_v = _version["value"]
        step = _find_step(from_v + 1 if direction == "forward" else from_v)
        if step is None:
            raise ValueError("no migration step for version %s" % from_v)
        transform = step.forward if direction == "forward" else step.backward
        to_v = step.version if direction == "forward" else step.version - 1
        expected_delta = (
            step.expect_rows_delta if direction == "forward" else -step.expect_rows_delta
        )
        rows_before = len(_rows)
        checksum_before = _checksum(_rows)
        step_started = time.monotonic()
        try:
            transform(_rows)
            actual_delta = len(_rows) - rows_before
            if actual_delta != expected_delta:
                raise MigrationError(
                    "row count changed by %s, expected %s" % (actual_delta, expected_delta)
                )
        except Exception as exc:
            ok = False
            error = "step %s->%s failed: %s" % (from_v, to_v, exc)
            failed_step = {"from": from_v, "to": to_v}
            break
        _version["value"] = to_v
        steps_done.append(
            {
                "from": from_v,
                "to": to_v,
                "description": step.description,
                "rows_before": rows_before,
                "rows_after": len(_rows),
                "checksum_before": checksum_before,
                "checksum_after": _checksum(_rows),
                "duration": time.monotonic() - step_started,
                "rollback_point": from_v,
            }
        )
    result = {
        "ok": ok,
        "action": "upgrade" if direction == "forward" else "downgrade",
        "from_version": start_version,
        "version": _version["value"],
        "operator": operator,
        "duration": time.monotonic() - started,
        "steps": steps_done,
        "rollback_point": start_version if ok else _version["value"],
        "error": error,
        "failed_step": failed_step,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    _log.append(result)
    return result


def upgrade(version, operator=None):
    """按序执行向前变换直到目标版本，每步前后校验行数和校验和。"""
    if version <= _version["value"]:
        raise ValueError("upgrade target must be higher than current version")
    return _run("forward", version, operator)


def downgrade(version, operator=None):
    """逆序执行向后变换直到目标版本；目标不低于当前版本时不支持。"""
    if version >= _version["value"]:
        raise NotImplementedError("downgrade only supports going to a lower version")
    return _run("backward", version, operator)
