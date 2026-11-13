"""多源对账：按业务键对齐多份数据，输出缺失、重复与字段差异，并按来源和类型归因。

两种模式：
- legacy（默认）：保持旧行为，只比前两份数据，diff 仅含 key/kind。
- multi（config 含 "mode": "multi" 或 "source_names"）：对齐全部来源，
  支持字段级口径转换、容差阈值、归因与可下钻报告，结果可 JSON 序列化。
"""

_MISSING = object()

_VALID_OPS = ("scale", "divide", "round", "int", "float", "str", "upper", "lower", "strip")


def reconcile(sources, config=None):
    """对多份数据按业务键对账，返回差异与归因报告。"""
    config = dict(config or {})
    _validate_sources(sources)
    mode = config.get("mode")
    if mode is None:
        mode = "multi" if "source_names" in config else "legacy"
    if mode == "legacy":
        return _reconcile_legacy(sources, config)
    if mode == "multi":
        return _reconcile_multi(sources, config)
    raise ValueError(f"未知对账模式: {mode!r}，支持 'legacy' 和 'multi'")


def _validate_sources(sources):
    if not isinstance(sources, (list, tuple)) or len(sources) < 2:
        raise ValueError(f"sources 至少需要两份数据，收到 {0 if sources is None else len(sources) if isinstance(sources, (list, tuple)) else type(sources).__name__} 份")
    for index, rows in enumerate(sources):
        if not isinstance(rows, (list, tuple)):
            raise ValueError(f"sources[{index}] 必须是行列表，收到 {type(rows).__name__}")


def _index_rows(rows, key_field, source_label):
    """按业务键索引一份数据，返回 (键->首行, 键->出现次数(仅重复键))。"""
    mapping = {}
    duplicates = {}
    for index, row in enumerate(rows):
        if key_field not in row:
            raise ValueError(f"{source_label} 第 {index} 行缺少业务键字段 {key_field!r}")
        key_value = row[key_field]
        if key_value in mapping:
            duplicates[key_value] = duplicates.get(key_value, 1) + 1
        else:
            mapping[key_value] = row
    return mapping, duplicates


def _sort_keys(keys):
    keys = list(keys)
    try:
        return sorted(keys)
    except TypeError:
        return sorted(keys, key=lambda v: (type(v).__name__, repr(v)))


def _reconcile_legacy(sources, config):
    """旧口径：只比前两份，第三份及以后忽略。"""
    key = config.get("key", "id")
    left_map, _ = _index_rows(sources[0], key, "sources[0]")
    right_map, _ = _index_rows(sources[1], key, "sources[1]")
    diffs = []
    for key_value in _sort_keys(set(left_map) | set(right_map)):
        a = left_map.get(key_value)
        b = right_map.get(key_value)
        if a is None or b is None:
            diffs.append({"key": key_value, "kind": "missing"})
        else:
            changed = [field for field in a if field in b and a[field] != b[field]]
            if changed:
                diffs.append({"key": key_value, "kind": "value", "fields": changed})
    return {"diffs": diffs, "sources": 2}


def _build_converters(conversions):
    by_field = {}
    for index, conv in enumerate(conversions):
        if not isinstance(conv, dict) or "field" not in conv or "op" not in conv:
            raise ValueError(f"conversions[{index}] 必须是含 'field' 和 'op' 的字典，收到 {conv!r}")
        if conv["op"] not in _VALID_OPS:
            raise ValueError(f"conversions[{index}] 未知口径转换 op: {conv['op']!r}，支持 {list(_VALID_OPS)}")
        by_field.setdefault(conv["field"], []).append(conv)
    return by_field


def _convert(value, conv, context):
    op = conv["op"]
    try:
        if op == "scale":
            return value * conv["factor"]
        if op == "divide":
            return value / conv["factor"]
        if op == "round":
            return round(value, conv.get("ndigits", 0))
        if op == "int":
            return int(value)
        if op == "float":
            return float(value)
        if op == "str":
            return str(value)
        if op == "upper":
            return str(value).upper()
        if op == "lower":
            return str(value).lower()
        if op == "strip":
            return str(value).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"口径转换失败（{context}，op={op!r}，value={value!r}）：{exc}") from exc
    return value


def _values_equal(a, b, tolerance):
    if a is _MISSING or b is _MISSING:
        return a is b
    if (tolerance is not None
            and isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)):
        return abs(a - b) <= tolerance
    return a == b


def _compare_values(key_value, field, values, present, tolerance):
    """把同一字段在各来源的取值按相等性分组，偏离共识的来源即归因对象。"""
    groups = []
    for name in present:
        value = values[name]
        for rep, members in groups:
            if _values_equal(rep, value, tolerance):
                members.append(name)
                break
        else:
            groups.append((value, [name]))
    if len(groups) == 1:
        return None
    groups.sort(key=lambda group: -len(group[1]))
    rep, members = groups[0]
    if len(members) > 1:
        consensus = None if rep is _MISSING else rep
        deviating = [n for _, ms in groups[1:] for n in ms]
    else:
        consensus = None
        deviating = list(present)
    return {
        "key": key_value,
        "kind": "value",
        "field": field,
        "values": {n: (None if values[n] is _MISSING else values[n]) for n in present},
        "consensus": consensus,
        "attribution": {"type": "value", "sources": deviating},
    }


def _reconcile_multi(sources, config):
    key_field = config.get("key", "id")
    names = list(config.get("source_names") or [f"source_{i}" for i in range(len(sources))])
    if len(names) != len(sources):
        raise ValueError(f"source_names 数量（{len(names)}）与 sources 数量（{len(sources)}）不一致")
    if len(set(names)) != len(names):
        raise ValueError(f"source_names 存在重复: {names}")
    tolerance = config.get("tolerance")
    if tolerance is not None and (
            not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance < 0):
        raise ValueError(f"tolerance 必须是非负数值，收到 {tolerance!r}")
    converters = _build_converters(config.get("conversions", []))

    maps = {}
    dup_counts = {}
    for name, rows in zip(names, sources):
        mapping, duplicates = _index_rows(rows, key_field, f"来源 {name!r}")
        maps[name] = mapping
        dup_counts[name] = duplicates

    all_keys = _sort_keys(set().union(*[set(m) for m in maps.values()]))
    diffs = []
    for key_value in all_keys:
        present = [n for n in names if key_value in maps[n]]
        for name in present:
            if key_value in dup_counts[name]:
                diffs.append({
                    "key": key_value,
                    "kind": "duplicate",
                    "source": name,
                    "count": dup_counts[name][key_value],
                    "attribution": {"type": "duplicate", "sources": [name]},
                })
        absent = [n for n in names if key_value not in maps[n]]
        if absent:
            diffs.append({
                "key": key_value,
                "kind": "missing",
                "present_in": present,
                "missing_from": absent,
                "attribution": {"type": "missing", "sources": absent},
            })
        if len(present) < 2:
            continue
        fields = set()
        for name in present:
            fields.update(maps[name][key_value])
        fields.discard(key_field)
        for field in sorted(fields, key=repr):
            values = {}
            for name in present:
                value = maps[name][key_value].get(field, _MISSING)
                if value is not _MISSING:
                    for conv in converters.get(field, []):
                        targets = conv.get("sources")
                        if targets is None or name in targets:
                            value = _convert(value, conv, f"来源 {name!r} 键 {key_value!r} 字段 {field!r}")
                values[name] = value
            diff = _compare_values(key_value, field, values, present, tolerance)
            if diff:
                diffs.append(diff)

    return {
        "mode": "multi",
        "key": key_field,
        "sources": names,
        "config": {
            "key": key_field,
            "source_names": names,
            "tolerance": tolerance,
            "conversions": config.get("conversions", []),
        },
        "diffs": diffs,
        "report": _build_report(names, diffs),
    }


def _build_report(names, diffs):
    """汇总报告：by_type / by_source 下钻，by_key 定位明细。"""
    by_type = {}
    by_source = {n: {"missing": 0, "duplicate": 0, "value": 0, "total": 0} for n in names}
    by_key = {}
    for index, diff in enumerate(diffs):
        kind = diff["kind"]
        by_type[kind] = by_type.get(kind, 0) + 1
        for name in diff["attribution"]["sources"]:
            by_source[name][kind] += 1
            by_source[name]["total"] += 1
        by_key.setdefault(str(diff["key"]), []).append(index)
    return {
        "total": len(diffs),
        "by_type": by_type,
        "by_source": by_source,
        "by_key": by_key,
    }
