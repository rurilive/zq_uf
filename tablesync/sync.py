"""表增量同步：主键 + 水位变更同步、墓碑删除、批次校验、水位回滚重放。

口径：
- 每行可带 ``_wm`` 表示该行变更水位，不带则取批次水位；
  水位不高于已应用版本的行视为过期，直接跳过。
- 删除以墓碑行 ``{"id": pk, "_deleted": True}`` 传播，未出现在批次里的行不动。
- 每批写前推算变更数、变更校验和与目标表行数/校验和，写后复核，
  不一致则整批回滚，水位不推进，返回 ``verified=False``。
- 水位只在批次复核通过后推进，并落检查点；``rollback`` 可回到任意检查点重放。
"""

import copy
import hashlib


def _new_state():
    return {
        "target": {},
        "versions": {},
        "tombstones": set(),
        "watermark": 0,
        "batches": [],
        "checkpoints": [(0, {}, {}, set())],
        "failed": False,
        "last_result": None,
    }


_tables = {}


def _before_commit(table, state):
    """批次写入后、复核前的扩展点，默认空操作；测试可注入故障验证停批。"""
    return None


def reset(table=None):
    """清空指定表；不传表名则清空全部表。"""
    if table is None:
        _tables.clear()
    else:
        _tables.pop(table, None)


def _state(table):
    if table not in _tables:
        _tables[table] = _new_state()
    return _tables[table]


def _checksum(rows):
    digest = hashlib.sha256()
    for key in sorted(rows):
        digest.update(str(key).encode("utf-8"))
        digest.update(repr(rows[key]).encode("utf-8"))
    return digest.hexdigest()


def _changes_digest(ops):
    digest = hashlib.sha256()
    for op in ops:
        digest.update(repr(op).encode("utf-8"))
    return digest.hexdigest()


def _plan(rows, watermark, versions):
    ops = []
    skipped = 0
    for row in rows:
        pk = row["id"]
        change_wm = row.get("_wm", watermark)
        if change_wm <= versions.get(pk, -1):
            skipped += 1
            continue
        if row.get("_deleted"):
            ops.append(("delete", pk, change_wm))
        else:
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            ops.append(("upsert", pk, change_wm, clean))
    return ops, skipped


def _apply(state, ops):
    written = 0
    deleted = 0
    for op in ops:
        pk = op[1]
        state["versions"][pk] = op[2]
        if op[0] == "delete":
            state["target"].pop(pk, None)
            state["tombstones"].add(pk)
            deleted += 1
        else:
            state["target"][pk] = dict(op[3])
            state["tombstones"].discard(pk)
            written += 1
    return written, deleted


def _project(base, ops):
    projected = copy.deepcopy(base)
    for op in ops:
        pk = op[1]
        if op[0] == "delete":
            projected.pop(pk, None)
        else:
            projected[pk] = dict(op[3])
    return projected


def _fail(state, result):
    state["failed"] = True
    state["last_result"] = result
    return result


def sync(table, rows, watermark=None, expected=None):
    """同步一个批次，返回批次号、水位、写入/删除/跳过数和校验结果。

    watermark 为批次水位，不传则在当前水位上自增；
    expected 可传入调用方推算的 changes/checksum/rows/target_checksum 做写前校验。
    """
    state = _state(table)
    batch_no = len(state["batches"]) + 1
    batch_wm = state["watermark"] + 1 if watermark is None else watermark
    ops, skipped = _plan(rows, batch_wm, state["versions"])

    changes = len(ops)
    change_checksum = _changes_digest(ops)
    projected = _project(state["target"], ops)
    projected_rows = len(projected)
    projected_checksum = _checksum(projected)

    if expected is not None:
        if (
            expected.get("changes", changes) != changes
            or expected.get("checksum", change_checksum) != change_checksum
            or expected.get("rows", projected_rows) != projected_rows
            or expected.get("target_checksum", projected_checksum) != projected_checksum
        ):
            return _fail(state, {
                "table": table,
                "batch": batch_no,
                "watermark": state["watermark"],
                "written": 0,
                "deleted": 0,
                "skipped": skipped,
                "changes": changes,
                "verified": False,
                "reason": "pre-check mismatch",
                "expected": expected,
                "actual": {
                    "changes": changes,
                    "checksum": change_checksum,
                    "rows": projected_rows,
                    "target_checksum": projected_checksum,
                },
            })

    baseline = {
        "target": copy.deepcopy(state["target"]),
        "versions": copy.deepcopy(state["versions"]),
        "tombstones": set(state["tombstones"]),
    }
    written, deleted = _apply(state, ops)
    _before_commit(table, state)
    actual_rows = len(state["target"])
    actual_checksum = _checksum(state["target"])

    if actual_rows != projected_rows or actual_checksum != projected_checksum:
        state["target"] = baseline["target"]
        state["versions"] = baseline["versions"]
        state["tombstones"] = baseline["tombstones"]
        return _fail(state, {
            "table": table,
            "batch": batch_no,
            "watermark": state["watermark"],
            "written": 0,
            "deleted": 0,
            "skipped": skipped,
            "changes": changes,
            "verified": False,
            "reason": "post-check mismatch",
            "expected": {"rows": projected_rows, "checksum": projected_checksum},
            "actual": {"rows": actual_rows, "checksum": actual_checksum},
        })

    state["failed"] = False
    state["watermark"] = max(state["watermark"], batch_wm)
    state["checkpoints"].append((
        state["watermark"],
        copy.deepcopy(state["target"]),
        copy.deepcopy(state["versions"]),
        set(state["tombstones"]),
    ))
    result = {
        "table": table,
        "batch": batch_no,
        "watermark": state["watermark"],
        "written": written,
        "deleted": deleted,
        "skipped": skipped,
        "changes": changes,
        "verified": True,
        "reason": None,
        "expected": {
            "changes": changes,
            "checksum": change_checksum,
            "rows": projected_rows,
            "target_checksum": projected_checksum,
        },
        "actual": {"rows": actual_rows, "checksum": actual_checksum},
    }
    state["batches"].append(result)
    state["last_result"] = result
    return result


def rollback(watermark, table=None):
    """回滚到指定水位的检查点；不传表名时要求水位在全库唯一。"""
    if watermark is None:
        raise NotImplementedError("rollback(None) is not supported; pass a confirmed watermark")
    if table is not None:
        targets = [table] if table in _tables else []
    else:
        targets = [
            name
            for name, state in _tables.items()
            if any(wm == watermark for wm, _, _, _ in state["checkpoints"])
        ]
    if not targets:
        raise LookupError(f"no checkpoint at watermark {watermark!r}")
    if len(targets) > 1:
        raise ValueError(f"watermark {watermark!r} exists in multiple tables; pass table=")

    state = _state(targets[0])
    match = None
    for checkpoint in state["checkpoints"]:
        if checkpoint[0] == watermark:
            match = checkpoint
    if match is None:
        raise LookupError(f"no checkpoint at watermark {watermark!r}")
    wm, target, versions, tombstones = match
    state["target"] = copy.deepcopy(target)
    state["versions"] = copy.deepcopy(versions)
    state["tombstones"] = set(tombstones)
    state["watermark"] = wm
    state["failed"] = False
    state["batches"] = [item for item in state["batches"] if item["watermark"] <= wm]
    state["last_result"] = None
    return {"table": targets[0], "watermark": wm}


def _progress_one(name, state):
    return {
        "table": name,
        "watermark": state["watermark"],
        "batches": len(state["batches"]),
        "rows": len(state["target"]),
        "tombstones": len(state["tombstones"]),
        "failed": state["failed"],
        "last_result": state["last_result"],
    }


def progress(table=None):
    """按表查询同步进度；不传表名返回全部表。"""
    if table is not None:
        return _progress_one(table, _state(table))
    return {name: _progress_one(name, state) for name, state in _tables.items()}


def snapshot(table=None):
    """返回当前目标表数据；不传表名时合并全表，主键冲突需指定表名。"""
    if table is not None:
        return copy.deepcopy(_state(table)["target"])
    merged = {}
    for state in _tables.values():
        for pk, row in state["target"].items():
            if pk in merged:
                raise ValueError(f"primary key {pk!r} exists in multiple tables; pass table=")
            merged[pk] = copy.deepcopy(row)
    return merged
