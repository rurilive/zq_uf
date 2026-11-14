"""结构差异影响分析。

比较字段名、类型、默认值、可空和主键，按破坏性分级；
结合血缘推导受影响的下游表和字段，输出影响路径与修复建议。
"""

LEVELS = ("critical", "major", "minor")
_PRIORITY = {level: rank for rank, level in enumerate(LEVELS)}

_SUGGESTIONS = {
    "removed": "确认下游无引用后删除，或先标记废弃再下线",
    "type_changed": "评估类型转换与数据迁移成本，同步通知下游改造",
    "pk_removed": "主键变更破坏关联与去重语义，需迁移下游关联键",
    "pk_added": "新增主键前保证历史数据唯一且非空",
    "nullable_tightened": "先回填历史空值，再收紧非空约束",
    "nullable_relaxed": "确认下游能正确处理空值",
    "default_removed": "下游写入需显式提供该字段取值",
    "default_changed": "确认新旧默认值口径一致，必要时回填历史数据",
    "default_added": "确认默认值不改变历史空值语义",
    "added_required": "为新增非空字段提供默认值或回填历史数据",
    "added": "安全变更，同步通知下游即可",
}


def _field_map(schema):
    return {f["name"]: f for f in schema.get("fields") or []}


def _is_pk(field):
    return bool(field.get("primary_key") or field.get("pk"))


def _is_nullable(field):
    return field.get("nullable", True)


def _item(field, kind, level, detail):
    return {
        "field": field,
        "kind": kind,
        "level": level,
        "priority": _PRIORITY[level],
        "detail": detail,
        "suggestion": _SUGGESTIONS[kind],
    }


def _compare_common(name, old_field, new_field):
    items = []
    old_type, new_type = old_field.get("type"), new_field.get("type")
    if old_type is not None and new_type is not None and old_type != new_type:
        items.append(_item(name, "type_changed", "critical",
                           "类型 %s -> %s" % (old_type, new_type)))
    if _is_pk(old_field) and not _is_pk(new_field):
        items.append(_item(name, "pk_removed", "critical", "主键被移除"))
    elif not _is_pk(old_field) and _is_pk(new_field):
        items.append(_item(name, "pk_added", "major", "新增主键约束"))
    if _is_nullable(old_field) and not _is_nullable(new_field):
        items.append(_item(name, "nullable_tightened", "major", "可空变为非空"))
    elif not _is_nullable(old_field) and _is_nullable(new_field):
        items.append(_item(name, "nullable_relaxed", "minor", "非空变为可空"))
    old_has = "default" in old_field
    new_has = "default" in new_field
    if old_has and not new_has:
        items.append(_item(name, "default_removed", "major",
                           "默认值 %r 被移除" % (old_field["default"],)))
    elif not old_has and new_has:
        items.append(_item(name, "default_added", "minor",
                           "新增默认值 %r" % (new_field["default"],)))
    elif old_has and new_has and old_field["default"] != new_field["default"]:
        items.append(_item(name, "default_changed", "minor",
                           "默认值 %r -> %r" % (old_field["default"], new_field["default"])))
    return items


def diff(old, new):
    """比较两版表结构，返回增删字段名列表和按优先级排序的差异项。"""
    old_fields = _field_map(old)
    new_fields = _field_map(new)
    added = [name for name in new_fields if name not in old_fields]
    removed = [name for name in old_fields if name not in new_fields]

    items = []
    for name in removed:
        items.append(_item(name, "removed", "critical", "字段被删除"))
    for name in added:
        field = new_fields[name]
        if not _is_nullable(field) and "default" not in field:
            items.append(_item(name, "added_required", "major", "新增非空且无默认值字段"))
        else:
            items.append(_item(name, "added", "minor", "新增字段"))
    for name in old_fields:
        if name in new_fields:
            items.extend(_compare_common(name, old_fields[name], new_fields[name]))

    items.sort(key=lambda item: (item["priority"], item["field"]))
    payload = {"added": added, "removed": removed, "items": items}
    table = new.get("name") or old.get("name")
    if table:
        payload["table"] = table
    return payload


def _normalize_lineage(lineage):
    edges = {}
    if isinstance(lineage, dict):
        pairs = ((src, dst) for src, dsts in lineage.items() for dst in dsts)
    else:
        pairs = ((e["from"], e["to"]) for e in lineage)
    for src, dst in pairs:
        edges.setdefault(src, []).append(dst)
    return edges


def _paths_from(edges, start):
    paths = []
    queue = [(start, [start])]
    while queue:
        node, path = queue.pop(0)
        downstream = [n for n in edges.get(node, []) if n not in path]
        if not downstream:
            if len(path) > 1:
                paths.append(path)
            continue
        for nxt in downstream:
            queue.append((nxt, path + [nxt]))
    return paths


def _table_of(node):
    return node.split(".", 1)[0] if "." in node else node


def impact(diff_payload, lineage=None):
    """结合血缘输出每个差异项的下游影响表、字段和影响路径。"""
    if not lineage:
        return []
    edges = _normalize_lineage(lineage)
    table = diff_payload.get("table")
    items = diff_payload.get("items")
    if items is None:
        items = (
            [_item(n, "removed", "critical", "字段被删除") for n in diff_payload.get("removed", [])]
            + [_item(n, "added", "minor", "新增字段") for n in diff_payload.get("added", [])]
        )

    results = []
    for item in items:
        starts = []
        if table:
            starts.append("%s.%s" % (table, item["field"]))
        starts.append(item["field"])
        paths = []
        for start in starts:
            if start in edges:
                paths.extend(_paths_from(edges, start))
                break
        if not paths:
            continue
        affected_tables = sorted({_table_of(node) for path in paths for node in path[1:]})
        affected_fields = sorted({node for path in paths for node in path[1:]})
        results.append({
            "field": item["field"],
            "kind": item["kind"],
            "level": item["level"],
            "priority": item["priority"],
            "affected_tables": affected_tables,
            "affected_fields": affected_fields,
            "paths": paths,
            "suggestion": item["suggestion"],
        })
    results.sort(key=lambda r: (r["priority"], r["field"]))
    return results
