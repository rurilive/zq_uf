"""按版本收敛的变更应用：版本向量、墓碑、幂等、冲突记录、批量原子。"""

_rows = {}
_conflicts = []


class EventError(ValueError):
    """事件非法或批量校验失败，状态保持不变。"""


def reset():
    _rows.clear()
    _conflicts.clear()


def conflicts():
    """累计冲突日志，每条保留两侧的值、版本向量和来源。"""
    return [dict(entry) for entry in _conflicts]


def _validate(events):
    if isinstance(events, (str, bytes)) or not isinstance(events, (list, tuple)):
        raise EventError("events 必须是事件列表")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise EventError(f"事件 {index} 不是字典: {event!r}")
        key = event.get("key")
        if not isinstance(key, str) or not key:
            raise EventError(f"事件 {index} 缺少非空字符串 key: {event!r}")
        if not isinstance(event.get("row"), dict):
            raise EventError(f"事件 {index} (key={key}) 缺少字典 row")
        version = event.get("version")
        if version is not None and (
            not isinstance(version, dict)
            or not all(
                isinstance(k, str) and isinstance(v, int) and v >= 0
                for k, v in version.items()
            )
        ):
            raise EventError(
                f"事件 {index} (key={key}) 的 version 必须是 str->非负int 字典"
            )


def _compare(new, old):
    keys = set(new) | set(old)
    greater = any(new.get(k, 0) > old.get(k, 0) for k in keys)
    less = any(new.get(k, 0) < old.get(k, 0) for k in keys)
    if greater and less:
        return "concurrent"
    if greater:
        return "after"
    if less:
        return "before"
    return "equal"


def _public(entry):
    row = dict(entry["row"])
    row["deleted"] = entry["deleted"]
    row["version"] = dict(entry["version"]) if entry["version"] is not None else {}
    return row


def apply(events, dry_run=False):
    """批量应用事件，按版本向量收敛；任一事件非法则整批不落库（原子）。

    返回 {"applied", "changes", "conflicts", "discarded"}。
    dry_run=True 时只在 changes 中预览将变化的行，不改任何状态。
    """
    _validate(events)
    working = {key: dict(entry) for key, entry in _rows.items()}
    applied = 0
    changes = []
    new_conflicts = []
    discarded = []
    for event in events:
        key = event["key"]
        row = event["row"]
        version = event.get("version")
        current = working.get(key)
        incoming = {
            "row": dict(row),
            "version": dict(version) if version is not None else None,
            "deleted": bool(row.get("deleted", False)),
            "source": event.get("source"),
        }
        if current is None or version is None or current["version"] is None:
            order = "after"  # 缺版本信息时退化为按到达顺序覆盖
        else:
            order = _compare(version, current["version"])
        if order == "after":
            working[key] = incoming
            applied += 1
            changes.append(
                {
                    "key": key,
                    "before": _public(current) if current is not None else None,
                    "after": _public(incoming),
                }
            )
        elif order == "concurrent" or (
            order == "equal" and current["row"] != incoming["row"]
        ):
            new_conflicts.append(
                {
                    "key": key,
                    "existing": {
                        "row": dict(current["row"]),
                        "version": dict(current["version"]),
                        "source": current["source"],
                    },
                    "incoming": {
                        "row": dict(row),
                        "version": dict(version),
                        "source": event.get("source"),
                    },
                }
            )
            discarded.append({"event": event, "reason": "conflict"})
        else:
            reason = "duplicate" if order == "equal" else "stale"
            discarded.append({"event": event, "reason": reason})
    if not dry_run:
        _rows.clear()
        _rows.update(working)
        _conflicts.extend(new_conflicts)
    return {
        "applied": applied,
        "changes": changes,
        "conflicts": new_conflicts,
        "discarded": discarded,
    }


def snapshot():
    """当前收敛结果：key -> 行（含 version 向量和 deleted 墓碑）。"""
    return {key: _public(entry) for key, entry in _rows.items()}
