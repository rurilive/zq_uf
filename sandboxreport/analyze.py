"""沙箱报告解析与按样本汇总。

入口：

* :func:`parse` —— 解析单份报告，返回按行为归类、重复合并后的行为条目、
  静态特征以及异常项；行为按首次出现的时间顺序排列，关键行为标出秒数。
* :func:`parse_batch` —— 批量解析同一批样本，格式不认识的报告保留原文并单独报错。
* :func:`summary` —— 按样本给出次数统计，可按行为类型筛选。
* :func:`merge_samples` —— 合并查看同一批样本，可按行为类型筛选。
"""

from .errors import ReportParseError
from .parsers import parse_raw
from .rules import (
    ANOMALY_RULES,
    API_HINT_TABLE,
    CANONICAL_TYPES,
    KEY_RULES,
)

# sample_id -> 单样本分析结果
_REPORTS = {}
# 解析失败的报告原文，避免信息丢失
_UNPARSED = []


def reset():
    """清空已解析报告与失败记录，主要供测试使用。"""
    _REPORTS.clear()
    _UNPARSED.clear()


def _is_key(behavior_type, action):
    for pattern in KEY_RULES.get(behavior_type, ()):
        if pattern.search(action):
            return True
    return False


def _match_anomaly(behavior_type, action):
    """返回命中的异常规则 (rule_id, reason)，未命中返回 None。"""
    for rule_id, rule_type, pattern, reason in ANOMALY_RULES:
        if rule_type == behavior_type and pattern.search(action):
            return rule_id, reason
    return None


def _merge_entries(entries):
    """按 (类型, 动作) 合并重复行为，记录次数，按首次出现秒数排序。"""
    merged = {}
    order = 0
    for entry in entries:
        key = (entry["type"], entry["action"])
        seconds = entry["seconds"]
        if key not in merged:
            hit = _match_anomaly(entry["type"], entry["action"])
            reasons = []
            rule_ids = []
            if hit:
                rule_ids.append(hit[0])
                reasons.append(hit[1])
            if entry.get("suspicious"):
                rule_ids.append("REPORT_FLAGGED")
                text = entry.get("reason") or "报告原文显式标记为异常"
                if text not in reasons:
                    reasons.append(text)
            if entry["type"] not in CANONICAL_TYPES:
                rule_ids.append("UNKNOWN_BEHAVIOR_TYPE")
                reasons.append(f"未知行为类型: {entry['type']}")

            item = {
                "type": entry["type"],
                "action": entry["action"],
                "count": 0,
                "first_seconds": seconds,
                "last_seconds": seconds,
                "seconds": [],
                "key": _is_key(entry["type"], entry["action"]) or bool(rule_ids),
                "anomaly": bool(rule_ids),
                "anomaly_rules": rule_ids,
                "anomaly_reasons": reasons,
                "_order": order,
            }
            merged[key] = item
            order += 1

        item = merged[key]
        item["count"] += 1
        if seconds is not None:
            item["seconds"].append(seconds)
            # first_seconds 取最早出现秒数，保证时间顺序不受报告罗列顺序影响。
            if (
                item["first_seconds"] is None
                or seconds < item["first_seconds"]
            ):
                item["first_seconds"] = seconds
            if (
                item["last_seconds"] is None
                or seconds > item["last_seconds"]
            ):
                item["last_seconds"] = seconds
        # 重复出现仍可能带来新的显式异常理由。
        if entry.get("suspicious") and "REPORT_FLAGGED" not in item["anomaly_rules"]:
            item["anomaly_rules"].append("REPORT_FLAGGED")
            item["anomaly_reasons"].append(
                entry.get("reason") or "报告原文显式标记为异常"
            )
            item["anomaly"] = True
            item["key"] = True

    items = list(merged.values())
    items.sort(
        key=lambda item: (
            item["first_seconds"] is None,
            item["first_seconds"] if item["first_seconds"] is not None else 0,
            item["_order"],
        )
    )
    for item in items:
        del item["_order"]
    return items


def _static_hints(static):
    """从 capabilities / apis / imports 推断样本应具备的行为类型。"""
    hinted = set()
    fields = []
    for name in ("capabilities", "apis", "imports"):
        value = static.get(name)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, (list, tuple)):
            fields.extend(str(part) for part in value)
    blob = " ".join(fields)
    if not blob:
        return hinted
    for behavior_type, pattern in API_HINT_TABLE:
        if pattern.search(blob):
            hinted.add(behavior_type)
    return hinted


def _check_static_mismatch(static, observed_types):
    """静态特征与行为结论双向对账，对不上的类型单独列出。"""
    hinted = _static_hints(static)
    if not hinted:
        return []
    observed = {name for name in observed_types if name in CANONICAL_TYPES}
    mismatches = []
    for behavior_type in sorted(hinted - observed):
        mismatches.append(
            {
                "type": behavior_type,
                "direction": "declared_but_absent",
                "reason": "静态特征暗示该类行为，但运行时未观测到",
            }
        )
    for behavior_type in sorted(observed - hinted):
        mismatches.append(
            {
                "type": behavior_type,
                "direction": "observed_but_undeclared",
                "reason": "运行时观测到该类行为，但静态特征中无对应 API/能力",
            }
        )
    return mismatches


def _flat_anomalies(behaviors):
    return [
        {
            "type": item["type"],
            "action": item["action"],
            "count": item["count"],
            "first_seconds": item["first_seconds"],
            "rules": list(item["anomaly_rules"]),
            "reasons": list(item["anomaly_reasons"]),
        }
        for item in behaviors
        if item["anomaly"]
    ]


def parse(report):
    """解析单份沙箱报告。

    返回字段：``id``、``format``、``behaviors``（合并去重后的行为条目，
    按首次出现时间排序）、``static``（静态特征，与行为结论放在一起）、
    ``anomalies``（异常行为清单）、``static_mismatches``（静态特征与行为
    对不上的类型）。不认识的报告格式抛 :class:`ReportParseError`，
    原文保留在异常对象的 ``report`` 属性上。
    """
    parsed = parse_raw(report)
    behaviors = _merge_entries(parsed["entries"])
    observed_types = {item["type"] for item in behaviors}
    mismatches = _check_static_mismatch(parsed["static"], observed_types)
    result = {
        "id": parsed["id"],
        "format": parsed["format"],
        "behaviors": behaviors,
        "static": parsed["static"],
        "anomalies": _flat_anomalies(behaviors),
        "static_mismatches": mismatches,
    }
    _REPORTS[parsed["id"]] = result
    return result


def parse_batch(reports):
    """批量解析同一批样本的报告。

    每份报告独立处理：单份格式不认识时保留原文并记录错误，不影响其余样本。
    返回 ``{"parsed": [成功结果...], "failed": [{"error", "report"}...]}``。
    """
    parsed, failed = [], []
    for report in reports:
        try:
            parsed.append(parse(report))
        except ReportParseError as exc:
            record = {"error": exc.message, "report": exc.report}
            failed.append(record)
            _UNPARSED.append(record)
    return {"parsed": parsed, "failed": failed}


def _filter_by_type(behaviors, behavior_type):
    if behavior_type is None:
        return list(behaviors)
    if behavior_type not in CANONICAL_TYPES:
        raise ValueError(f"unknown behavior type: {behavior_type!r}")
    return [item for item in behaviors if item["type"] == behavior_type]


def summary(sample_id, behavior_type=None):
    """按样本汇总：行为条目、发生次数、静态特征和异常项。

    ``behavior_type`` 非空时只统计该类型（network/file/process/registry），
    可用于按类型筛选行为条目。
    """
    if sample_id not in _REPORTS:
        raise ValueError(f"unknown sample id: {sample_id!r}")
    result = _REPORTS[sample_id]
    behaviors = _filter_by_type(result["behaviors"], behavior_type)

    by_type = {}
    for item in behaviors:
        by_type[item["type"]] = by_type.get(item["type"], 0) + item["count"]

    return {
        "id": sample_id,
        "format": result["format"],
        "behavior_type": behavior_type,
        "total": sum(item["count"] for item in behaviors),
        "unique": len(behaviors),
        "by_type": by_type,
        "behaviors": behaviors,
        "static": result["static"],
        "anomalies": [
            item for item in result["anomalies"]
            if behavior_type is None or item["type"] == behavior_type
        ],
        "static_mismatches": result["static_mismatches"],
    }


def merge_samples(sample_ids=None, behavior_type=None):
    """合并查看同一批样本的行为条目，可按行为类型筛选。

    相同 (类型, 动作) 在样本间继续合并，``samples`` 记录涉及的样本及
    各自出现次数。``sample_ids`` 为空时合并全部已解析样本。
    """
    if sample_ids is None:
        selected = list(_REPORTS.values())
    else:
        unknown = [sid for sid in sample_ids if sid not in _REPORTS]
        if unknown:
            raise ValueError(f"unknown sample id: {unknown[0]!r}")
        selected = [_REPORTS[sid] for sid in sample_ids]

    merged = {}
    order = 0
    for result in selected:
        for item in _filter_by_type(result["behaviors"], behavior_type):
            key = (item["type"], item["action"])
            if key not in merged:
                merged[key] = {
                    "type": item["type"],
                    "action": item["action"],
                    "count": 0,
                    "samples": [],
                    "first_seconds": item["first_seconds"],
                    "key": item["key"],
                    "anomaly": item["anomaly"],
                    "anomaly_rules": list(item["anomaly_rules"]),
                    "anomaly_reasons": list(item["anomaly_reasons"]),
                    "_order": order,
                }
                order += 1
            aggregate = merged[key]
            aggregate["count"] += item["count"]
            aggregate["samples"].append(
                {"id": result["id"], "count": item["count"]}
            )
            if (
                item["first_seconds"] is not None
                and (
                    aggregate["first_seconds"] is None
                    or item["first_seconds"] < aggregate["first_seconds"]
                )
            ):
                aggregate["first_seconds"] = item["first_seconds"]

    items = list(merged.values())
    items.sort(
        key=lambda item: (
            item["first_seconds"] is None,
            item["first_seconds"] if item["first_seconds"] is not None else 0,
            item["_order"],
        )
    )
    for item in items:
        del item["_order"]

    by_type = {}
    for item in items:
        by_type[item["type"]] = by_type.get(item["type"], 0) + item["count"]
    return {
        "sample_ids": [result["id"] for result in selected],
        "behavior_type": behavior_type,
        "total": sum(item["count"] for item in items),
        "unique": len(items),
        "by_type": by_type,
        "behaviors": items,
        "anomalies": [item for item in items if item["anomaly"]],
    }


def types():
    """列出已出现过的行为类型，标准类型在前、保持固定顺序。"""
    seen = set()
    for result in _REPORTS.values():
        for item in result["behaviors"]:
            seen.add(item["type"])
    ordered = [name for name in CANONICAL_TYPES if name in seen]
    ordered.extend(sorted(seen - set(CANONICAL_TYPES)))
    return {"types": ordered}


def static_mismatches():
    """列出静态特征与行为结论对不上的样本。"""
    return [
        {"id": result["id"], "mismatches": result["static_mismatches"]}
        for result in _REPORTS.values()
        if result["static_mismatches"]
    ]


def unparsed():
    """返回解析失败的报告原文与错误信息（格式不认识时的留档）。"""
    return list(_UNPARSED)
