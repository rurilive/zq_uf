"""采集结果多版本合并。

判定顺序（同 key 的行分组后逐级比较）：

1. 版本高者胜；版本缺失按基线版本 0 补齐，并标记 ``version_filled``，
   缺版本行之间的相对次序由来源优先级补齐判定；
2. 同版本比来源优先级（``source_priority`` 可配置，排在前面的来源优先，
   未配置的来源同档，且排在所有已配置来源之后）；
3. 同来源不同批次按时间排序（``time`` 支持 epoch 数字或 ISO 字符串，
   缺时间回落到批次到达顺序，后者胜）；
4. 同版本、同来源优先级、不同来源且内容不同 => 冲突，冲突两侧的原始行
   全部保留，赢家仍按时间/批次顺序确定性选出。

合并产物可通过 :func:`replay` 按同一规则重算，通过 :func:`export` 导出 JSON。
纯标准库实现。
"""

import copy
import json
import os
from datetime import datetime

__all__ = ["configure", "merge", "report", "replay", "export", "reset"]

# 行内的元数据字段，其余字段视为业务负载（用于内容比对）。
_META_KEYS = ("key", "batch", "version", "source", "time")

# 模块级默认配置与最近一次合并状态（report() 读取最近一次合并）。
_config = {"source_priority": []}
_last = {"snapshot": None, "result": None, "report": None}


def configure(source_priority=None):
    """配置来源优先级，列表中越靠前的来源优先级越高。"""
    _config["source_priority"] = list(source_priority or [])
    return {"source_priority": list(_config["source_priority"])}


def reset():
    """清空配置与最近一次合并状态（主要供测试使用）。"""
    _config["source_priority"] = []
    _last["snapshot"] = None
    _last["result"] = None
    _last["report"] = None


def _parse_version(value):
    """返回 (有效版本, 是否为缺失补齐)。缺失或无法解析按基线 0 补齐。"""
    if value is None:
        return 0, True
    if isinstance(value, bool):
        return int(value), False
    if isinstance(value, (int, float)):
        return value, False
    try:
        return float(value), False
    except (TypeError, ValueError):
        return 0, True


def _parse_time(value, fallback):
    """时间转可比较的浮点数；epoch 数字直接用，ISO 字符串转时间戳；
    缺失或无法解析时回落到批次序号。返回 (时间值, 是否有可用时间)。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), True
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp(), True
        except ValueError:
            pass
    return float(fallback), False


def _source_rank(source, priority):
    """来源优先级位次：配置列表内的来源取其下标，未配置来源同档且殿后。"""
    try:
        return priority.index(source)
    except ValueError:
        return len(priority)


def _normalize(batches):
    """把输入批次规整成带批次/版本/来源印记的行信封，不修改入参。"""
    normalized = []
    order = 0
    for batch_index, batch in enumerate(batches or []):
        batch_id = batch.get("batch", batch_index)
        batch_time = batch.get("time")
        rows = []
        for raw in batch.get("rows", []):
            raw = dict(raw)
            version, version_filled = _parse_version(raw.get("version"))
            source = raw.get("source")
            explicit_time = raw.get("time", batch_time)
            time_value, has_time = _parse_time(explicit_time, batch_index)
            rows.append({
                "key": raw.get("key"),
                "batch": batch_id,
                "version": version,
                "version_filled": version_filled,
                "source": source,
                "time": explicit_time,
                "order": order,
                "_has_time": has_time,
                "_time_value": time_value,
                "raw": raw,
                "payload": {k: v for k, v in raw.items() if k not in _META_KEYS},
            })
            order += 1
        normalized.append({"batch": batch_id, "time": batch_time, "rows": rows})
    return normalized


def _public(row):
    """去掉内部字段，得到可导出/可回放的行信封。"""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _pick_latest(rows):
    """同来源内按时间（再按到达顺序）选最新的一行。"""
    return max(rows, key=lambda r: (r["_time_value"], r["order"]))


def _resolve(rows, priority):
    """对同 key 的一组行做判定，返回 (赢家, 冲突侧列表或None, [(行, 丢弃原因)])。"""
    dropped = []

    # 第 1 级：版本高者胜。
    top_version = max(r["version"] for r in rows)
    leaders = [r for r in rows if r["version"] == top_version]
    for r in rows:
        if r["version"] != top_version:
            dropped.append((r, "lower_version"))

    # 第 2 级：同版本比来源优先级。
    best_rank = min(_source_rank(r["source"], priority) for r in leaders)
    tier = [r for r in leaders
            if _source_rank(r["source"], priority) == best_rank]
    for r in leaders:
        if _source_rank(r["source"], priority) != best_rank:
            dropped.append((r, "lower_source_priority"))

    winner = _pick_latest(tier)
    distinct_sources = {r["source"] for r in tier}
    distinct_payloads = {json.dumps(r["payload"], sort_keys=True, ensure_ascii=False)
                         for r in tier}

    # 第 4 级：同档且来源不同、内容不同 => 冲突，两侧原始行都保留。
    if len(distinct_sources) > 1 and len(distinct_payloads) > 1:
        return winner, tier, dropped

    # 无冲突：同来源按时间/批次排序，或内容完全相同的重复行。
    for r in tier:
        if r is winner:
            continue
        if r["payload"] == winner["payload"]:
            reason = "identical_duplicate"
        elif r["_has_time"] and winner["_has_time"] \
                and r["_time_value"] != winner["_time_value"]:
            reason = "older_time"
        else:
            reason = "older_batch"
        dropped.append((r, reason))
    return winner, None, dropped


def _decision(conflict_sides, dropped):
    if conflict_sides is not None:
        return "conflict"
    if not dropped:
        return "sole"
    reasons = {reason for _, reason in dropped}
    labels = [
        ("lower_version", "version_win"),
        ("lower_source_priority", "source_priority_win"),
        ("older_time", "time_win"),
        ("older_batch", "batch_order_win"),
        ("identical_duplicate", "identical"),
    ]
    for reason, label in labels:
        if reason in reasons:
            return label
    return "won"


def _execute(norm_batches, priority):
    """归一化后的批次 -> (merge 返回值, report 返回值)，merge 与 replay 共用。"""
    groups = {}
    for batch in norm_batches:
        for row in batch["rows"]:
            groups.setdefault(row["key"], []).append(row)

    merged_rows = []
    conflicts = []
    dropped_rows = []

    for key in sorted(groups, key=lambda k: str(k)):
        winner, conflict_sides, dropped = _resolve(groups[key], priority)
        decision = _decision(conflict_sides, dropped)

        merged_rows.append({
            "key": key,
            "batch": winner["batch"],
            "version": winner["version"],
            "version_filled": winner["version_filled"],
            "source": winner["source"],
            "decision": decision,
            "row": _public(winner),
        })

        if conflict_sides is not None:
            conflicts.append({
                "key": key,
                "version": winner["version"],
                "reason": "same_version_equal_priority_distinct_source",
                "winner": _public(winner),
                "sides": [_public(r) for r in conflict_sides],
            })

        for row, reason in dropped:
            dropped_rows.append({
                "key": key,
                "reason": reason,
                "row": _public(row),
            })

    run_config = {"source_priority": list(priority)}
    result = {
        "merged": len(merged_rows),
        "conflicts": conflicts,
        "dropped": dropped_rows,
        "merged_rows": merged_rows,
    }
    report_data = {
        "rows": len(merged_rows),
        "conflicts": conflicts,
        "merged": merged_rows,
        "dropped": dropped_rows,
        "config": run_config,
    }
    return result, report_data


def merge(batches, source_priority=None):
    """按版本和来源合并多个批次。

    ``source_priority`` 可选，给出后仅本次合并生效，不改变
    :func:`configure` 设置的模块默认值，同时作为回放配置快照；
    缺省时使用模块默认配置。返回合并/冲突/丢弃三类产物，
    其中 ``merged`` 为合并后行数（整数）。
    """
    if source_priority is not None:
        priority = list(source_priority)
    else:
        priority = list(_config["source_priority"])

    norm_batches = _normalize(batches)
    snapshot = {
        "batches": copy.deepcopy(norm_batches),
        "config": {"source_priority": priority},
    }
    result, report_data = _execute(norm_batches, priority)

    _last["snapshot"] = snapshot
    _last["result"] = copy.deepcopy(result)
    _last["report"] = copy.deepcopy(report_data)
    return result


def report():
    """最近一次合并的报告：逐行给出来源与判定，以及冲突和丢弃明细。"""
    if _last["report"] is None:
        return {"rows": 0, "conflicts": [], "merged": [], "dropped": [],
                "config": {"source_priority": list(_config["source_priority"])}}
    return copy.deepcopy(_last["report"])


def _load_snapshot(snapshot):
    if snapshot is None:
        if _last["snapshot"] is None:
            raise RuntimeError("没有可回放的合并记录，请先调用 merge()")
        return copy.deepcopy(_last["snapshot"])
    if isinstance(snapshot, (str, os.PathLike)):
        with open(snapshot, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    if "snapshot" in snapshot:  # 接受 export() 导出的整包结构。
        snapshot = snapshot["snapshot"]
    return copy.deepcopy(snapshot)


def replay(snapshot=None):
    """按同一规则对合并快照重算，返回与当初 merge() 一致的结果。

    ``snapshot`` 可缺省（回放最近一次合并），也可传入 :func:`export`
    导出的字典或导出文件路径。
    """
    snap = _load_snapshot(snapshot)
    norm_batches = snap["batches"]
    priority = snap["config"]["source_priority"]
    result, _ = _execute(norm_batches, priority)
    return result


def export(path=None):
    """导出最近一次合并的配置、输入快照与产物为 JSON。

    传路径则写文件并返回路径；不传则返回 JSON 字符串。导出文件可直接
    传给 :func:`replay` 做跨进程回放。
    """
    if _last["snapshot"] is None:
        raise RuntimeError("没有可导出的合并记录，请先调用 merge()")
    payload = {
        "config": copy.deepcopy(_last["snapshot"]["config"]),
        "snapshot": copy.deepcopy(_last["snapshot"]),
        "result": copy.deepcopy(_last["result"]),
        "report": copy.deepcopy(_last["report"]),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path is None:
        return text
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.fspath(path)
