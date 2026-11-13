"""带版本和来源的冲突解决。

行可携带元数据：version（非负整数）、updated_at（时间戳）、
source（来源标识），deleted=True 表示墓碑。两端都带 version 时按
版本比较加策略裁决；都不带时退化为旧行为（远端覆盖本地）。
"""

META_KEYS = ("version", "updated_at", "source")
STRATEGIES = ("local", "remote", "manual")

_MISSING = object()


class SyncError(ValueError):
    """参数或行数据非法。"""


class ConflictError(SyncError):
    """存在冲突但当前策略无法自动解决。"""


class Conflict:
    """单个字段的冲突记录。"""

    def __init__(self, field, local, remote, winner, reason):
        self.field = field
        self.local = local
        self.remote = remote
        self.winner = winner
        self.reason = reason

    def __eq__(self, other):
        return (
            isinstance(other, Conflict)
            and vars(self) == vars(other)
        )

    def __repr__(self):
        def fmt(value):
            return "<missing>" if value is _MISSING else repr(value)

        return (
            f"Conflict(field={self.field!r}, local={fmt(self.local)}, "
            f"remote={fmt(self.remote)}, winner={self.winner!r}, "
            f"reason={self.reason!r})"
        )


class MergeResult(dict):
    """合并结果行，附带 conflicts（冲突列表）和 winner（胜方）。"""

    def __init__(self, row, conflicts, winner):
        super().__init__(row)
        self.conflicts = list(conflicts)
        self.winner = winner


def _validate_meta(row, side):
    missing = [k for k in META_KEYS if k not in row]
    if missing:
        raise SyncError(f"{side} 行缺少元数据字段: {', '.join(missing)}")
    version = row["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise SyncError(f"{side} 行 version 必须是非负整数, got {version!r}")
    updated_at = row["updated_at"]
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        raise SyncError(f"{side} 行 updated_at 必须是数字, got {updated_at!r}")
    source = row["source"]
    if not isinstance(source, str) or not source:
        raise SyncError(f"{side} 行 source 必须是非空字符串, got {source!r}")
    if not isinstance(row.get("deleted", False), bool):
        raise SyncError(f"{side} 行 deleted 必须是布尔值, got {row['deleted']!r}")


def _diff_fields(local, remote):
    fields = (set(local) | set(remote)) - set(META_KEYS)
    return sorted(
        f for f in fields if local.get(f, _MISSING) != remote.get(f, _MISSING)
    )


def merge(local, remote, strategy="remote", resolver=None):
    """合并两行，返回 MergeResult。

    strategy: "local" 本地优先 / "remote" 远端优先 / "manual" 人工，
    人工策略下 resolver(info) 必须返回 "local" 或 "remote"。
    """
    if strategy not in STRATEGIES:
        raise SyncError(f"未知策略 {strategy!r}, 支持: {', '.join(STRATEGIES)}")
    if ("version" in local) != ("version" in remote):
        raise SyncError("两端必须同时带 version 或同时不带")
    if "version" not in local:
        row = dict(local)
        row.update(remote)
        return MergeResult(row, [], "remote")

    _validate_meta(local, "local")
    _validate_meta(remote, "remote")

    lv, rv = local["version"], remote["version"]
    if lv != rv:
        winner = "local" if lv > rv else "remote"
        src = local if winner == "local" else remote
        return MergeResult(dict(src), [], winner)

    fields = _diff_fields(local, remote)
    if not fields:
        winner = "local" if local["updated_at"] > remote["updated_at"] else "remote"
        src = local if winner == "local" else remote
        return MergeResult(dict(src), [], winner)

    if strategy == "manual":
        if resolver is None:
            raise ConflictError("strategy='manual' 且存在冲突, 必须提供 resolver")
        winner = resolver(
            {"local": dict(local), "remote": dict(remote), "fields": fields}
        )
        if winner not in ("local", "remote"):
            raise SyncError(f"resolver 必须返回 'local' 或 'remote', got {winner!r}")
    else:
        winner = strategy

    reason = f"version 相同({lv}), 按策略 {strategy} 裁决"
    conflicts = [
        Conflict(f, local.get(f, _MISSING), remote.get(f, _MISSING), winner, reason)
        for f in fields
    ]
    src = local if winner == "local" else remote
    return MergeResult(dict(src), conflicts, winner)
