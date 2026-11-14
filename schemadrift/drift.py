"""基线比对与差异分级。

差异只有四类：
- added   新增列
- removed 删除列
- type    类型/可空性变化（可空性属于列定义约束，因此并入 type 类）
- default 默认值变化（含“有无默认值”的变化）

级别只有三档：high / medium / low，按对下游取数的实际影响判定。
敏感列的默认值只比较哈希，报告中不回显明文。
"""

from .fingerprint import is_sensitive_name

LEVELS = ("low", "medium", "high")
LEVEL_RANK = {"low": 1, "medium": 2, "high": 3}
_LEVEL_RANK = LEVEL_RANK

KINDS = ("added", "removed", "type", "default")

SUGGESTIONS = {
    "added": {
        "high": "新增列是 NOT NULL 且无默认值：先补默认值或在写入链路同步加列，否则插入会失败。",
        "low": "新增列可空或带默认值：确认下游 ETL 的 SELECT * 与列顺序假设，一般无需回滚。",
        "medium": "新增列的可空/默认值信息不完整：确认列约束后再决定是否同步下游模型。",
    },
    "removed": {
        "high": "删除列会让下游查询直接失败：先确认无 SELECT/依赖，再同步删除下游模型与血缘。",
        "medium": "删除列的可空/默认值信息不完整：确认该列确无下游依赖后再清理。",
        "low": "删除列影响有限：同步清理下游引用。",
    },
    "type": {
        "high": "类型或可空性收紧可能造成截断/精度丢失/写入失败：先评估存量数据与下游强转，再灰度切换。",
        "medium": "类型或可空性放宽会改变下游的空值与类型假设：更新下游模型与校验规则。",
        "low": "类型或可空性变化影响较小：更新下游文档与类型映射即可。",
    },
    "default": {
        "high": "NOT NULL 列的默认值被移除：存量写入可能失败，先恢复默认值或同步写入链路。",
        "medium": "默认值变化会改变取数口径：确认下游对空值的兜底逻辑后同步基线。",
        "low": "默认值变化影响较小：同步基线并知会下游。",
    },
}


def _normalise(value):
    """把 table 或 snapshot 统一成带 column_specs 的快照。

    旧式快照只有列名时，只保留 name/sensitive，其余属性标记为未知，
    避免把“缺少类型信息”误判成类型漂移。
    """
    if not isinstance(value, dict):
        raise TypeError("base/target 必须是 table 或 snapshot：%r" % (value,))
    if "column_specs" in value and "fingerprint" in value:
        return value
    if "column_specs" in value:
        return value

    specs = {}
    for column in value.get("columns") or []:
        name = str(column["name"] if isinstance(column, dict) else column)
        specs[name] = {"name": name, "sensitive": is_sensitive_name(name)}
    return {
        "table": value.get("table") or value.get("name"),
        "env": str(value.get("env") or "default"),
        "columns": sorted(specs),
        "column_specs": specs,
        "fingerprint": None,
        "legacy": True,
    }


def _known(spec, key):
    if not isinstance(spec, dict):
        return False
    known = spec.get("known_attributes")
    if known is not None:
        return key in known
    return key in spec


def _nullable_of(spec):
    return spec.get("nullable") if _known(spec, "nullable") else None


def _has_default_of(spec):
    return spec.get("has_default") if _known(spec, "has_default") else None


def _redacted(spec):
    return bool(spec.get("sensitive") or is_sensitive_name(spec.get("name")))


def _added_level(spec):
    """新增列：约束未知先按 medium；NOT NULL 且无默认值最危险（写入失败）。"""
    nullable = _nullable_of(spec)
    has_default = _has_default_of(spec)
    if nullable is None:
        return "medium"
    if nullable is False and has_default is False:
        return "high"
    return "low"


def _removed_level(spec):
    """删除列一律 high；仅当约束信息完全未知时降为 medium。"""
    if _nullable_of(spec) is None:
        return "medium"
    return "high"


def _type_level(attribute, before, after):
    if attribute == "nullable":
        # False -> True 是放宽，True -> False 是收紧。
        return "high" if after is False else "medium"
    return "high"


def _default_level(target_spec):
    """默认值移除且仍为 NOT NULL 时升为 high。"""
    has_default = _has_default_of(target_spec)
    nullable = _nullable_of(target_spec)
    if has_default is False and nullable is False:
        return "high"
    return "medium"


def _make_change(kind, column, level, attribute=None, before=None, after=None,
                 sensitive=False, redacted=False, note=None):
    suggestion = SUGGESTIONS[kind].get(level) or SUGGESTIONS[kind]["medium"]
    change = {
        "kind": kind,
        "column": column,
        "level": level,
        "suggestion": suggestion,
    }
    if attribute is not None:
        change["attribute"] = attribute
    if before is not None or after is not None:
        change["before"] = before
        change["after"] = after
    if sensitive:
        change["sensitive"] = True
    if redacted:
        change["redacted"] = True
    if note:
        change["note"] = note
    return change


def _default_view(spec):
    """默认值的报告视图：敏感列只给哈希，非敏感列给明文。"""
    if not spec:
        return None
    if not _known(spec, "has_default"):
        return None
    if not spec["has_default"]:
        return None
    if _redacted(spec):
        return spec.get("default_hash")
    return spec.get("default", spec.get("default_hash"))


def _compare_column(column, base_spec, target_spec):
    changes = []

    # 类型
    if _known(base_spec, "type") and _known(target_spec, "type"):
        before_type = base_spec["type"]
        after_type = target_spec["type"]
        if before_type != after_type:
            changes.append(_make_change(
                "type", column, _type_level("type", before_type, after_type),
                attribute="type", before=before_type, after=after_type,
                sensitive=_redacted(target_spec),
            ))

    # 可空性（并入 type 类）
    if _known(base_spec, "nullable") and _known(target_spec, "nullable"):
        before_nullable = base_spec["nullable"]
        after_nullable = target_spec["nullable"]
        if before_nullable != after_nullable:
            changes.append(_make_change(
                "type", column, _type_level("nullable", before_nullable, after_nullable),
                attribute="nullable", before=before_nullable, after=after_nullable,
                sensitive=_redacted(target_spec),
            ))

    # 默认值：统一比较哈希，敏感列明文不出现。
    if _known(base_spec, "has_default") and _known(target_spec, "has_default"):
        before_hash = base_spec.get("default_hash")
        after_hash = target_spec.get("default_hash")
        if (base_spec["has_default"], before_hash) != (target_spec["has_default"], after_hash):
            redacted = _redacted(target_spec) or _redacted(base_spec)
            changes.append(_make_change(
                "default", column, _default_level(target_spec),
                attribute="default",
                before=_default_view(base_spec),
                after=_default_view(target_spec),
                sensitive=redacted,
                redacted=redacted,
                note="敏感列默认值仅比较哈希，不保存明文。" if redacted else None,
            ))

    return changes


def _meets_min_level(level, min_level):
    if not min_level:
        return True
    return _LEVEL_RANK.get(level, 0) >= _LEVEL_RANK.get(min_level, 0)


def diff(base, target, *, columns=None, kinds=None, min_level=None):
    """比较两个结构，输出差异、级别与修复建议。

    base / target 可以是 snapshot() 的结果，也可以是原始 table。
    columns  按列名过滤（字符串或可迭代），如 "note" 或 ["note"]。
    kinds    按差异类别过滤，取值 added/removed/type/default。
    min_level 只保留不低于该级别（low/medium/high）的差异。
    """
    base = _normalise(base)
    target = _normalise(target)

    if isinstance(columns, str):
        columns = [columns]
    kind_set = None
    if kinds is not None:
        if isinstance(kinds, str):
            kinds = [kinds]
        kind_set = {str(kind) for kind in kinds}
        unknown = kind_set - set(KINDS)
        if unknown:
            raise ValueError("未知差异类别：%s" % sorted(unknown))

    base_specs = base.get("column_specs") or {}
    target_specs = target.get("column_specs") or {}
    changes = []

    for column in sorted(set(target_specs) - set(base_specs)):
        level = _added_level(target_specs[column])
        changes.append(_make_change(
            "added", column, level,
            sensitive=_redacted(target_specs[column]),
        ))

    for column in sorted(set(base_specs) - set(target_specs)):
        level = _removed_level(base_specs[column])
        changes.append(_make_change(
            "removed", column, level,
            sensitive=_redacted(base_specs[column]),
        ))

    for column in sorted(set(base_specs) & set(target_specs)):
        changes.extend(_compare_column(column, base_specs[column], target_specs[column]))

    all_changes = list(changes)
    if columns is not None:
        columns = {str(column) for column in columns}
        changes = [change for change in changes if change["column"] in columns]
    if kind_set is not None:
        changes = [change for change in changes if change["kind"] in kind_set]
    if min_level:
        if min_level not in LEVELS:
            raise ValueError("未知级别：%s" % min_level)
        changes = [change for change in changes if _meets_min_level(change["level"], min_level)]

    changes.sort(key=lambda change: (-_LEVEL_RANK[change["level"]], KINDS.index(change["kind"]), change["column"]))

    summary = {kind: 0 for kind in KINDS}
    level_counts = {level: 0 for level in LEVELS}
    for change in changes:
        summary[change["kind"]] += 1
        level_counts[change["level"]] += 1

    overall = None
    if changes:
        overall = max((change["level"] for change in changes), key=lambda lvl: _LEVEL_RANK[lvl])

    # 兼容旧接口：added/removed 为列名列表，仍表示本次返回的差异。
    return {
        "table": target.get("table") or base.get("table"),
        "base_env": base.get("env"),
        "target_env": target.get("env"),
        "base_fingerprint": base.get("fingerprint"),
        "target_fingerprint": target.get("fingerprint"),
        "fingerprint_changed": (
            base.get("fingerprint") != target.get("fingerprint")
            if base.get("fingerprint") and target.get("fingerprint")
            else bool(all_changes)
        ),
        "has_drift": bool(all_changes),
        "level": overall,
        "added": sorted(change["column"] for change in changes if change["kind"] == "added"),
        "removed": sorted(change["column"] for change in changes if change["kind"] == "removed"),
        "changes": changes,
        "summary": summary,
        "level_counts": level_counts,
    }


def filter_report(report, *, columns=None, kinds=None, min_level=None):
    """对已生成的差异报告按字段/类别/级别过滤，不改动原报告。"""
    if not isinstance(report, dict) or "changes" not in report:
        raise TypeError("report 必须是 diff() 的结果：%r" % (report,))
    if isinstance(columns, str):
        columns = [columns]
    if isinstance(kinds, str):
        kinds = [kinds]
    result = dict(report)
    changes = list(report["changes"])
    if columns is not None:
        wanted = {str(column) for column in columns}
        changes = [change for change in changes if change["column"] in wanted]
    if kinds is not None:
        wanted = {str(kind) for kind in kinds}
        changes = [change for change in changes if change["kind"] in wanted]
    if min_level:
        if min_level not in LEVELS:
            raise ValueError("未知级别：%s" % min_level)
        changes = [change for change in changes if _meets_min_level(change["level"], min_level)]

    result["changes"] = changes
    summary = {kind: 0 for kind in KINDS}
    level_counts = {level: 0 for level in LEVELS}
    for change in changes:
        summary[change["kind"]] += 1
        level_counts[change["level"]] += 1
    result["summary"] = summary
    result["level_counts"] = level_counts
    result["added"] = sorted(c["column"] for c in changes if c["kind"] == "added")
    result["removed"] = sorted(c["column"] for c in changes if c["kind"] == "removed")
    result["level"] = max((c["level"] for c in changes), key=lambda lvl: _LEVEL_RANK[lvl]) if changes else None
    result["has_drift"] = bool(changes)
    return result


