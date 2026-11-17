"""沙箱报告解析入口。

对外入口：
  parse(report)            解析单份报告，行为按类型归类、合并计数、按时间排序
  summary(sample_id, ...)  按样本查看，可按行为类型筛选
  batch(sample_ids, ...)   同批样本合并查看，结论可按行为类型汇总
  inconsistent()           静态特征与行为对不上的样本
  types()                  规范行为类型清单
"""

from .errors import UnknownReportFormatError
from .formats import parse_report
from .rules import TYPES, anomaly_reason, check_static, is_key

_REPORTS = {}


def reset():
    """清空已解析的报告。"""
    _REPORTS.clear()


def _validate_type_filter(behavior_type):
    if behavior_type is not None and behavior_type not in TYPES:
        raise ValueError(
            "unknown behavior type: {!r}, valid types: {}".format(
                behavior_type, ", ".join(TYPES)))


def _merge_events(events):
    """同一 (类型, 动作) 合并为一条，记录次数，按首次出现秒数排序。"""
    grouped = {}
    order = []
    for event in events:
        key = (event["type"], event["norm"])
        if key not in grouped:
            grouped[key] = {
                "type": event["type"],
                "action": event["action"],
                "count": 0,
                "seconds": [],
                "is_key": is_key(event["type"], event["action"]),
                "anomaly": anomaly_reason(event["type"], event["action"]),
                "_first": None,
            }
            order.append(key)
        entry = grouped[key]
        entry["count"] += 1
        if isinstance(event["seconds"], (int, float)) and not isinstance(
                event["seconds"], bool):
            entry["seconds"].append(event["seconds"])
            if entry["_first"] is None or event["seconds"] < entry["_first"]:
                entry["_first"] = event["seconds"]

    # 稳定排序：先按首次发生秒数，没有秒数的条目排最后并保持原顺序
    indexed = list(enumerate(grouped[key] for key in order))
    indexed.sort(key=lambda pair: (
        pair[1]["_first"] is None,
        pair[1]["_first"] if pair[1]["_first"] is not None else 0,
        pair[0],
    ))
    merged = []
    for _, entry in indexed:
        entry["seconds"].sort()
        entry.pop("_first", None)
        merged.append(entry)
    return merged


def _counts_by_type(behaviors):
    counts = {}
    for behavior in behaviors:
        bucket = counts.setdefault(
            behavior["type"], {"entries": 0, "events": 0})
        bucket["entries"] += 1
        bucket["events"] += behavior["count"]
    return counts


def _filter_behaviors(behaviors, behavior_type):
    if behavior_type is None:
        return list(behaviors)
    return [b for b in behaviors if b["type"] == behavior_type]


def parse(report):
    """解析一份沙箱报告。

    返回：
      id          样本 id
      format      识别到的报告格式（flat / sectioned / text）
      behaviors   按 (类型, 动作) 合并后的行为条目，按首次出现秒数排序；
                  每条带 count（出现次数）、seconds（各次发生秒数）、
                  is_key（关键行为）、anomaly（异常原因，非异常为 None）
      static      静态特征，与行为结论放在同一份结果里
      anomalies   异常行为条目（behaviors 中 anomaly 非空的子集）
      mismatches  静态特征与行为对不上的条目
      consistent  对账是否一致
      total       行为条目数 / 原始事件数
      by_type     按类型汇总的条目数与事件数

    报告格式不认识时抛 UnknownReportFormatError，原文在异常的 raw 字段里。
    """
    sample_id, events, static, fmt = parse_report(report)
    behaviors = _merge_events(events)
    mismatches = check_static(static, events)
    result = {
        "id": sample_id,
        "format": fmt,
        "behaviors": behaviors,
        "static": static,
        "anomalies": [b for b in behaviors if b["anomaly"]],
        "mismatches": mismatches,
        "consistent": not mismatches,
        "total": {
            "entries": len(behaviors),
            "events": sum(b["count"] for b in behaviors),
        },
        "by_type": _counts_by_type(behaviors),
    }
    _REPORTS[sample_id] = result
    return result


def summary(sample_id, behavior_type=None):
    """按样本查看解析结论，可传 behavior_type 只看某一类行为。"""
    if sample_id not in _REPORTS:
        raise ValueError("unknown sample id: {!r}".format(sample_id))
    _validate_type_filter(behavior_type)
    result = _REPORTS[sample_id]
    behaviors = _filter_behaviors(result["behaviors"], behavior_type)
    return {
        "id": result["id"],
        "format": result["format"],
        "static": result["static"],
        "behaviors": behaviors,
        "anomalies": _filter_behaviors(result["anomalies"], behavior_type),
        "mismatches": result["mismatches"],
        "consistent": result["consistent"],
        "total": {
            "entries": len(behaviors),
            "events": sum(b["count"] for b in behaviors),
        },
        "by_type": _counts_by_type(behaviors),
    }


def batch(sample_ids=None, behavior_type=None):
    """合并查看同一批样本。

    sample_ids 为 None 时合并全部已解析样本；同名行为跨样本合并，
    count 拆成总事件数和涉及样本数，异常与对账问题单列。
    """
    _validate_type_filter(behavior_type)
    if sample_ids is None:
        selected = list(_REPORTS.values())
    else:
        unknown = [sid for sid in sample_ids if sid not in _REPORTS]
        if unknown:
            raise ValueError("unknown sample id(s): {}".format(
                ", ".join(map(repr, unknown))))
        selected = [_REPORTS[sid] for sid in sample_ids]

    grouped = {}
    order = []
    all_anomalies = []
    all_mismatches = []
    for result in selected:
        for behavior in _filter_behaviors(result["behaviors"], behavior_type):
            key = (behavior["type"], behavior["action"].lower())
            if key not in grouped:
                grouped[key] = {
                    "type": behavior["type"],
                    "action": behavior["action"],
                    "count": 0,
                    "samples": [],
                    "seconds": [],
                    "is_key": behavior["is_key"],
                    "anomaly": behavior["anomaly"],
                }
                order.append(key)
            entry = grouped[key]
            entry["count"] += behavior["count"]
            entry["seconds"].extend(behavior["seconds"])
            if result["id"] not in entry["samples"]:
                entry["samples"].append(result["id"])
        all_anomalies.extend(
            (result["id"], item)
            for item in _filter_behaviors(result["anomalies"], behavior_type)
        )
        all_mismatches.extend(
            (result["id"], item) for item in result["mismatches"]
        )

    for entry in grouped.values():
        entry["seconds"].sort()
    merged = [grouped[key] for key in order]
    merged.sort(key=lambda b: (b["seconds"][0] if b["seconds"] else 1 << 30))

    return {
        "sample_ids": [result["id"] for result in selected],
        "sample_count": len(selected),
        "behaviors": merged,
        "anomalies": [
            {"sample_id": sid, **behavior} for sid, behavior in all_anomalies
        ],
        "mismatches": [
            {"sample_id": sid, **mismatch}
            for sid, mismatch in all_mismatches
        ],
        "inconsistent_samples": sorted({
            sid for sid, _ in all_mismatches
        }),
        "total": {
            "entries": len(merged),
            "events": sum(b["count"] for b in merged),
        },
        "by_type": _counts_by_type(merged),
    }


def inconsistent():
    """静态特征与行为对不上的样本，逐条给出对账问题。"""
    return [
        {"id": result["id"], "mismatches": result["mismatches"]}
        for result in _REPORTS.values()
        if result["mismatches"]
    ]


def types():
    """规范行为类型清单（网络、文件、进程、注册表）。"""
    return {"types": list(TYPES)}
