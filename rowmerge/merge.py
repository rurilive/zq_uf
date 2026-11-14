"""数据行合并：版本向量 + 墓碑。

行可以携带 id（主键）、version（{来源: 计数} 版本向量）、updated_at（更新时间）、
source（来源）、deleted（墓碑标记），其余字段是数据值。

合并口径：
- 版本向量逐分量比较，旧版本不能覆盖新版本；
- 同版本按来源优先级仲裁，优先级相同依次看墓碑、更新时间、来源名、值；
- 并发版本保留两侧值并记入冲突；
- 删除写墓碑，旧更新不能复活。

每次 merge() 是一个批次，冲突、丢弃和结果摘要按批次归档，可按主键或批次回查。
"""

import copy
import json
import time

_META_KEYS = frozenset(("id", "version", "updated_at", "source", "deleted"))

_state = {}            # 主键 -> 当前收敛行（含墓碑）
_archive = []          # 批次审计归档
_batch_seq = 0
_source_priority = {}  # 来源 -> 优先级，数值越大越优先


def reset(include_priority=False):
    """清空合并状态、批次归档和批次序号。

    include_priority=True 时同时清空来源优先级登记。
    """
    global _state, _archive, _batch_seq, _source_priority
    _state = {}
    _archive = []
    _batch_seq = 0
    if include_priority:
        _source_priority = {}


def set_source_priority(mapping=None, *, replace=False):
    """登记来源优先级：数值越大越优先，未登记来源按 0。

    不传 mapping 只返回当前登记；replace=True 先清空再登记。
    也可以在 merge(..., source_priority={...}) 里按批次覆盖。
    """
    global _source_priority
    if replace:
        _source_priority = {}
    if mapping:
        _source_priority.update({str(k): int(v) for k, v in mapping.items()})
    return dict(_source_priority)


def merge(rows, source_priority=None, batch=None):
    """合并一批行，返回收敛结果、冲突和丢弃。

    每行的字段见模块说明；同一主键在一次调用里出现多次时按下面的规则收敛。
    返回值：{"rows": 全部收敛行（含墓碑）, "conflicts": [...],
             "discards": [...], "batch": 批次号}。
    """
    global _batch_seq

    priority = dict(_source_priority)
    if source_priority:
        priority.update({str(k): int(v) for k, v in source_priority.items()})

    batch_rows = list(rows)
    groups = {}
    for row in batch_rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a dict")
        if "id" not in row:
            raise ValueError("each row needs an id")
        groups.setdefault(row["id"], []).append(copy.deepcopy(row))

    _batch_seq += 1
    batch_id = _batch_seq if batch is None else batch

    conflicts = []
    discards = []
    touched = list(groups)

    for key, incoming in groups.items():
        winner, group_conflicts, group_discards = _resolve(
            key, incoming, _state.get(key), batch_id, priority
        )
        _state[key] = copy.deepcopy(winner)
        conflicts.extend(group_conflicts)
        discards.extend(group_discards)

    conflicts.sort(key=_record_sort_key)
    discards.sort(key=_record_sort_key)

    outcome = {
        key: {
            "source": _state[key].get("source"),
            "version": copy.deepcopy(_state[key].get("version")),
            "deleted": bool(_state[key].get("deleted")),
        }
        for key in touched
    }
    _archive.append(
        {
            "batch": batch_id,
            "seq": _batch_seq,
            "at": time.time(),
            "input": len(batch_rows),
            "keys": list(touched),
            "conflicts": copy.deepcopy(conflicts),
            "discards": copy.deepcopy(discards),
            "outcome": copy.deepcopy(outcome),
        }
    )

    return {
        "rows": copy.deepcopy(_state),
        "conflicts": copy.deepcopy(conflicts),
        "discards": copy.deepcopy(discards),
        "batch": batch_id,
    }


def report(key=None, batch=None):
    """返回冲突记录，可按主键或批次过滤；默认返回全部归档批次。"""
    records = []
    for entry in _archive:
        if batch is not None and entry["batch"] != batch:
            continue
        for conflict in entry["conflicts"]:
            if key is not None and conflict["key"] != key:
                continue
            records.append(copy.deepcopy(conflict))
    return records


def discards(key=None, batch=None):
    """返回丢弃记录，可按主键或批次过滤；默认返回全部归档批次。"""
    records = []
    for entry in _archive:
        if batch is not None and entry["batch"] != batch:
            continue
        for record in entry["discards"]:
            if key is not None and record["key"] != key:
                continue
            records.append(copy.deepcopy(record))
    return records


def lookup(key):
    """按主键回查当前行、冲突、丢弃和涉及的批次。"""
    return {
        "key": key,
        "row": copy.deepcopy(_state.get(key)),
        "conflicts": report(key=key),
        "discards": discards(key=key),
        "batches": [entry["batch"] for entry in _archive if key in entry["keys"]],
    }


def audit(batch=None):
    """返回批次审计归档；batch=None 返回全部批次。"""
    if batch is None:
        return copy.deepcopy(_archive)
    return [copy.deepcopy(entry) for entry in _archive if entry["batch"] == batch]


# --- 行与版本向量的内部工具 -------------------------------------------------


def _is_legacy(row):
    """没有 version 字段的行按到达顺序覆盖（兼容旧调用）。"""
    return row.get("version") is None


def _version(row):
    version = row.get("version")
    if version is None:
        return {}
    if not isinstance(version, dict):
        raise ValueError(f"row {row.get('id')!r}: version must be a dict")
    try:
        return {k: int(v) for k, v in version.items()}
    except (TypeError, ValueError):
        raise ValueError(f"row {row.get('id')!r}: version counters must be ints")


def _dominates(first, second):
    """first 的版本向量严格大于 second（逐分量不小于，且至少一个分量更大）。"""
    first_version = _version(first)
    second_version = _version(second)
    strictly_greater = False
    for component in set(first_version) | set(second_version):
        first_count = first_version.get(component, 0)
        second_count = second_version.get(component, 0)
        if first_count < second_count:
            return False
        if first_count > second_count:
            strictly_greater = True
    return strictly_greater


def _same_version(first, second):
    first_version = _version(first)
    second_version = _version(second)
    return all(
        first_version.get(component, 0) == second_version.get(component, 0)
        for component in set(first_version) | set(second_version)
    )


def _payload(row):
    return {key: value for key, value in row.items() if key not in _META_KEYS}


def _content_equal(first, second):
    return (
        bool(first.get("deleted")) == bool(second.get("deleted"))
        and _payload(first) == _payload(second)
    )


def _canonical_payload(row):
    try:
        return json.dumps(_payload(row), sort_keys=True, default=repr, ensure_ascii=False)
    except TypeError:
        return repr(sorted(_payload(row).items(), key=lambda item: repr(item[0])))


def _time_key(value):
    """更新时间排序键：数字 > 字符串 > 缺失，同类按值比较。"""
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (2, float(value), "")
    return (1, 0.0, str(value))


def _source_name(row):
    return "" if row.get("source") is None else str(row.get("source"))


def _priority_for(row, priority):
    return priority.get(_source_name(row), 0)


def _order_key(row, priority):
    """同版本/并发时的确定性仲裁顺序：墓碑、优先级、时间、来源名、值。

    墓碑排在最前：删除与并发更新相遇时删除优先，避免旧行复活。
    """
    return (
        1 if row.get("deleted") else 0,
        _priority_for(row, priority),
        _time_key(row.get("updated_at")),
        _source_name(row),
        _canonical_payload(row),
    )


def _signature(row):
    version = _version(row)
    return (
        repr(sorted(version.items(), key=lambda item: repr(item[0]))),
        _source_name(row),
        _time_key(row.get("updated_at")),
        bool(row.get("deleted")),
        _canonical_payload(row),
    )


def _side(row):
    return {
        "source": row.get("source"),
        "version": copy.deepcopy(row.get("version")),
        "updated_at": row.get("updated_at"),
        "deleted": bool(row.get("deleted")),
        "row": copy.deepcopy(row),
    }


def _resolution(winner, loser, priority):
    if bool(winner.get("deleted")) != bool(loser.get("deleted")):
        return "tombstone"
    if _priority_for(winner, priority) != _priority_for(loser, priority):
        return "source_priority"
    if _time_key(winner.get("updated_at")) != _time_key(loser.get("updated_at")):
        return "updated_at"
    if _source_name(winner) != _source_name(loser):
        return "source_name"
    return "payload"


def _conflict(key, batch_id, reason, winner, maximal, priority):
    sides = sorted(maximal, key=lambda row: _order_key(row, priority), reverse=True)
    loser = sides[1] if len(sides) > 1 else winner
    return {
        "key": key,
        "batch": batch_id,
        "reason": reason,
        "resolution": _resolution(winner, loser, priority),
        "winner": _side(winner),
        "sides": [_side(row) for row in sides],
        "values": [copy.deepcopy(row) for row in sides],
    }


def _discard(key, batch_id, reason, row, winner):
    return {
        "key": key,
        "batch": batch_id,
        "reason": reason,
        "source": row.get("source"),
        "version": copy.deepcopy(row.get("version")),
        "row": copy.deepcopy(row),
        "winner": _side(winner),
    }


def _record_sort_key(record):
    winner = record.get("winner") or {}
    return (
        str(record.get("key")),
        str(record.get("reason")),
        str(winner.get("source")),
        repr(winner.get("version")),
    )


def _resolve(key, incoming, existing, batch_id, priority):
    """对一个主键解一批输入，返回 (winner, conflicts, discards)。

    只有本批次提交却被拒绝的行才进丢弃记录；被替换的旧状态属于正常演进。
    """
    conflicts = []
    discards = []

    incoming_legacy = all(_is_legacy(row) for row in incoming)
    if incoming_legacy and (existing is None or _is_legacy(existing)):
        # 没有任何版本信息：按到达顺序后来者覆盖，不产生冲突/丢弃记录。
        return incoming[-1], conflicts, discards

    candidates = ([existing] if existing is not None else []) + list(incoming)

    unique = []
    seen = {}
    duplicates = []
    for candidate in candidates:
        signature = _signature(candidate)
        if signature in seen:
            if candidate is not existing:
                duplicates.append(candidate)
            continue
        seen[signature] = candidate
        unique.append(candidate)

    maximal = [
        candidate
        for candidate in unique
        if not any(
            _dominates(other, candidate)
            for other in unique
            if other is not candidate
        )
    ]
    winner = max(maximal, key=lambda row: _order_key(row, priority))

    if len(maximal) > 1 and any(
        not _content_equal(winner, other) for other in maximal
    ):
        reason = (
            "same_version"
            if all(_same_version(maximal[0], other) for other in maximal)
            else "concurrent"
        )
        conflicts.append(_conflict(key, batch_id, reason, winner, maximal, priority))

    for candidate in duplicates:
        discards.append(_discard(key, batch_id, "duplicate", candidate, winner))

    for candidate in incoming:
        if candidate is winner:
            continue
        if _signature(candidate) in seen and seen[_signature(candidate)] is not candidate:
            continue
        dominators = [
            other
            for other in unique
            if other is not candidate and _dominates(other, candidate)
        ]
        if dominators:
            tombstoned = not candidate.get("deleted") and any(
                other.get("deleted") and _dominates(other, candidate)
                for other in dominators
            )
            reason = "tombstoned" if tombstoned else "superseded_by_newer"
        elif _content_equal(candidate, winner):
            reason = "duplicate"
        else:
            reason = "conflict_loser"
        discards.append(_discard(key, batch_id, reason, candidate, winner))

    return winner, conflicts, discards
