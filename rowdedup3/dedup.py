"""按版本与完整度合并同主键数据行。

规则：
- 同主键比版本，高版本胜出；低版本记录不覆盖，并记入丢弃原因。
- 同版本比字段完整度，空值不覆盖有效值，有效字段可以互补。
- 同版本同字段出现多个不同的非空取值记为冲突：合并结果只落一版
  （取最完整的记录），但两侧原始记录、来源和取舍原因完整保留。
- 版本缺失时按时间补齐为"当时生效的版本"（该记录时间点之前最近的
  显式版本）；整组都没有版本时按到达时间依次补齐为 1..n。
- 合并过程是纯计算，回放只读不写，重复合并同一批记录结果不变。
"""

import json
import math

# 不参与字段完整度比较的元字段
_META_FIELDS = {
    "table", "key", "version", "ts", "time", "timestamp", "source", "_source",
}
_TIME_FIELDS = ("ts", "time", "timestamp")

# 持久状态：journal 保留全部来源记录，seen 保证重复回放幂等
_state = {
    "journal": [],
    "seen": set(),
    "seq": 0,
}


def reset():
    """清空状态（主要给测试和演示用）。"""
    _state["journal"] = []
    _state["seen"] = set()
    _state["seq"] = 0


def merge(rows):
    """合并一批记录，返回合并结果。

    已经见过的记录（内容相同）不会重复写入，因此对同一批数据重复
    merge 或 replay 不会改变已有结果。
    """
    for row in rows:
        ident = _identity(row)
        if ident in _state["seen"]:
            continue
        _state["seen"].add(ident)
        _state["seq"] += 1
        _state["journal"].append(_record(row, _state["seq"]))
    return _compute(_state["journal"])


def report():
    """按表汇总当前合并状态，保留冲突、丢弃原因和来源。"""
    result = _compute(_state["journal"])
    return {
        "rows": len(result["rows"]),
        "deduped": result["deduped"],
        "conflicts": result["conflicts"],
        "dropped": result["dropped"],
        "dropped_details": result["dropped_details"],
        "stats": result["stats"],
    }


def replay(rows=None):
    """回放合并结果，不修改已有状态。

    不传参数时回放当前 journal；传入原始记录时按传入顺序重新计算。
    """
    if rows is None:
        return _compute(_state["journal"])
    records = [_record(row, i + 1) for i, row in enumerate(rows)]
    return _compute(records)


def export():
    """导出保留来源的原始记录、合并结果和按表统计。"""
    result = _compute(_state["journal"])
    return {
        "records": [dict(rec["row"]) for rec in _state["journal"]],
        "rows": result["rows"],
        "stats": result["stats"],
    }


# ---------- 内部实现 ----------

def _identity(row):
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


def _record(row, seq):
    table = row.get("table", "")
    key = row.get("key")
    source = row.get("source") or row.get("_source") or "%s:%s@%d" % (table, key, seq)
    return {
        "id": seq,
        "table": table,
        "key": key,
        "version": row.get("version"),
        "fields": {k: v for k, v in row.items() if k not in _META_FIELDS},
        "time": _time_key(row, seq),
        "source": source,
        "row": dict(row),
    }


def _time_key(row, seq):
    for name in _TIME_FIELDS:
        value = row.get(name)
        if not _is_empty(value):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (0, float(value))
            return (1, str(value))
    # 没有时间戳时用到达顺序近似时间
    return (0, float(seq))


def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _value_key(value):
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _norm_version(value):
    """把版本归一化成可比较的 tuple，兼容数字和字符串版本。"""
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (0, float(value))
    text = str(value).strip()
    try:
        return (0, float(text))
    except ValueError:
        return (1, text)


def _has_version(rec):
    return not _is_empty(rec["version"])


def _effective_versions(recs):
    """为组内每条记录确定生效版本。

    - 显式版本直接使用；
    - 缺失版本时，取它时间点之前最近的显式版本（当时生效的版本），
      之前没有任何显式版本则取最早的显式版本；
    - 整组都没有显式版本时，按时间顺序补齐为 1..n。
    """
    explicit = [rec for rec in recs if _has_version(rec)]
    effective = {}

    if not explicit:
        for rank, rec in enumerate(recs, 1):
            effective[rec["id"]] = {
                "norm": (0, float(rank)),
                "value": rank,
                "filled": True,
                "reason": "version_filled_from_time",
            }
        return effective

    explicit_ids = {rec["id"] for rec in explicit}
    earliest = explicit[0]
    latest = None
    for rec in recs:
        if rec["id"] in explicit_ids:
            latest = rec
            effective[rec["id"]] = {
                "norm": _norm_version(rec["version"]),
                "value": rec["version"],
                "filled": False,
            }
        elif latest is not None:
            # 时间点之前已有显式版本：按"当时生效的版本"补齐
            effective[rec["id"]] = {
                "norm": _norm_version(latest["version"]),
                "value": latest["version"],
                "filled": True,
                "reason": "version_filled_from_time",
                "filled_from": latest["source"],
            }
        else:
            # 早于最早显式版本：补齐为低于所有显式版本的版本，
            # 保证不会被误判成与更高版本并列
            effective[rec["id"]] = {
                "norm": (-1, 0.0),
                "value": None,
                "filled": True,
                "reason": "version_filled_before_first",
                "filled_from": earliest["source"],
            }
    return effective


def _completeness(rec):
    return sum(1 for value in rec["fields"].values() if not _is_empty(value))


def _compute(journal):
    groups = {}
    for rec in journal:
        groups.setdefault((str(rec["table"]), rec["key"]), []).append(rec)

    rows = []
    conflicts = []
    dropped = []
    stats = {}

    for (table, key), recs in sorted(groups.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        recs = sorted(recs, key=lambda rec: (rec["time"], rec["id"]))
        effective = _effective_versions(recs)

        by_version = {}
        for rec in recs:
            by_version.setdefault(effective[rec["id"]]["norm"], []).append(rec)
        winning_norm = max(by_version)
        winning_version = effective[by_version[winning_norm][0]["id"]]["value"]

        # 低版本记录整体丢弃，原因和来源保留
        for norm, members in by_version.items():
            if norm == winning_norm:
                continue
            for rec in members:
                dropped.append({
                    "table": table,
                    "key": key,
                    "source": rec["source"],
                    "reason": "older_version",
                    "version": effective[rec["id"]]["value"],
                    "winning_version": winning_version,
                    "row": rec["row"],
                })

        row, group_conflicts, group_dropped = _merge_version(
            table, key, by_version[winning_norm], effective,
            _group_reason(recs, effective),
        )
        rows.append(row)
        conflicts.extend(group_conflicts)
        dropped.extend(group_dropped)

        table_stat = stats.setdefault(table, {
            "input": 0, "kept": 0, "deduped": 0, "conflicts": 0, "dropped": 0,
        })
        table_stat["input"] += len(recs)
        table_stat["kept"] += 1
        table_stat["conflicts"] += len(group_conflicts)
        table_stat["dropped"] += len(group_dropped) + sum(
            1 for norm, members in by_version.items() if norm != winning_norm
            for _ in members
        )

    for table, stat in stats.items():
        stat["deduped"] = stat["input"] - stat["kept"]

    return {
        "rows": rows,
        "deduped": len(journal) - len(rows),
        "conflicts": conflicts,
        "dropped": len(dropped),
        "dropped_details": dropped,
        "stats": stats,
    }


def _group_reason(recs, effective):
    if len(recs) == 1:
        return "single_record"
    if all(effective[rec["id"]]["filled"] for rec in recs):
        return "version_filled_from_time"
    norms = {effective[rec["id"]]["norm"] for rec in recs}
    if len(norms) > 1:
        return "higher_version"
    return "same_version_merged"


def _merge_version(table, key, members, effective, reason):
    """同版本内按字段完整度合并，冲突两侧都保留。"""
    # 最完整优先，其次时间较新，最后编号较大，保证结果确定
    ranked = sorted(
        members,
        key=lambda rec: (_completeness(rec), rec["time"], rec["id"]),
        reverse=True,
    )
    base = ranked[0]
    display_version = effective[base["id"]]["value"]

    merged_fields = {}
    field_names = []
    for rec in ranked:
        for name in rec["fields"]:
            if name not in field_names:
                field_names.append(name)

    conflicts = []
    decisions = {}
    for name in field_names:
        values = [(rec, rec["fields"].get(name)) for rec in ranked]
        non_empty = [(rec, value) for rec, value in values if not _is_empty(value)]

        if not non_empty:
            merged_fields[name] = None
            continue

        base_value = base["fields"].get(name)
        if not _is_empty(base_value):
            chosen = base_value
        else:
            # 空值不覆盖有效值：从其他记录补全
            chosen = non_empty[0][1]
        merged_fields[name] = chosen

        distinct = {}
        for rec, value in non_empty:
            distinct.setdefault(_value_key(value), []).append((rec, value))
        if len(distinct) > 1:
            conflicts.append({
                "table": table,
                "key": key,
                "version": display_version,
                "field": name,
                "values": [value for _, value in non_empty],
                "chosen": chosen,
                "sides": [
                    {
                        "source": rec["source"],
                        "version": effective[rec["id"]]["value"],
                        "version_filled": effective[rec["id"]]["filled"],
                        "value": value,
                        "row": rec["row"],
                    }
                    for rec, value in non_empty
                ],
            })

        for rec, value in values:
            if rec is base:
                continue
            if _is_empty(value):
                decision = "empty_ignored"
            elif _is_empty(base_value):
                decision = "filled_empty"
            elif _value_key(value) == _value_key(chosen):
                decision = "duplicate_value"
            else:
                decision = "conflict_kept"
            decisions.setdefault(rec["id"], {})[name] = decision

    dropped = []
    for rec in ranked[1:]:
        record_decisions = decisions.get(rec["id"], {})
        conflict_fields = sorted(n for n, d in record_decisions.items() if d == "conflict_kept")
        kept_fields = sorted(n for n, d in record_decisions.items() if d == "filled_empty")
        ignored_fields = sorted(n for n, d in record_decisions.items() if d == "empty_ignored")

        if conflict_fields:
            reason = "conflict_kept"
        elif kept_fields:
            reason = "merged_complementary"
        elif ignored_fields:
            reason = "empty_ignored"
        else:
            reason = "duplicate"
        dropped.append({
            "table": table,
            "key": key,
            "source": rec["source"],
            "reason": reason,
            "version": effective[rec["id"]]["value"],
            "conflict_fields": conflict_fields,
            "kept_fields": kept_fields,
            "ignored_empty_fields": ignored_fields,
            "row": rec["row"],
        })

    merged_row = {
        "table": table,
        "key": key,
        "version": display_version,
    }
    for ts_field in _TIME_FIELDS:
        if ts_field in base["row"]:
            merged_row[ts_field] = base["row"][ts_field]
            break
    merged_row.update(merged_fields)
    merged_row["provenance"] = {
        "chosen_source": base["source"],
        "reason": reason,
        "sources": [
            {
                "source": rec["source"],
                "version": effective[rec["id"]]["value"],
                "version_filled": effective[rec["id"]]["filled"],
                "filled_from": effective[rec["id"]].get("filled_from"),
                "chosen": rec is base,
                "decisions": decisions.get(rec["id"], {}),
            }
            for rec in ranked
        ],
    }
    return merged_row, conflicts, dropped
