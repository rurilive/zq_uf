"""把各来源的时间归一到同一时区并排成一条时间线。"""

from datetime import datetime, timezone

MERGE_WINDOW = 5.0    # 相邻事件合并窗口（秒）
SKEW_THRESHOLD = 5.0  # 同一段内允许的最大时间偏差（秒），超过记为冲突

_CASES = {}  # case -> [原始证据]，同一案件多次提交可合并查看
_LAST = {"events": [], "untimed": [], "conflicts": [], "merged": 0, "skew": 0.0, "case": None}


def _iso(instant):
    return instant.isoformat().replace("+00:00", "Z")


def _parse_time(raw):
    """解析 ISO 时间，返回 (归一到 UTC 的瞬时, 是否假定了时区)；失败返回 (None, False)。"""
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc), True
    return parsed.astimezone(timezone.utc), False


def _merge_segment(segment, skew_threshold):
    """同一主体/对象/动作且相邻的一段记录合并成一条事件，保留全部来源与原始值。"""
    first = segment[0]
    times = [{
        "id": entry["record"]["id"],
        "source": entry["source"],
        "raw": entry["record"].get("time"),
        "normalized": _iso(entry["instant"]),
    } for entry in segment]
    skew = (segment[-1]["instant"] - segment[0]["instant"]).total_seconds() if len(segment) > 1 else 0.0
    return {
        "id": first["record"]["id"],
        "ids": [entry["record"]["id"] for entry in segment],
        "time": _iso(segment[0]["instant"]),
        "subject": first["subject"],
        "object": first["object"],
        "action": first["action"],
        "sources": sorted({entry["source"] for entry in segment}),
        "times": times,
        "skew": skew,
        "conflict": skew > skew_threshold,
        "assumed_timezone": any(entry["assumed"] for entry in segment),
        "records": [dict(entry["record"]) for entry in segment],
    }


def _build(evidence, merge_window, skew_threshold):
    """归一、按主体和对象归类、相邻合并，返回 (事件, 缺时标注, 冲突, 合并条数, 最大偏差)。"""
    parsed, untimed = [], []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            untimed.append({"record": item, "reason": "证据项不是字典"})
            continue
        record = dict(item)
        record.setdefault("id", "e%d" % index)
        instant, assumed = _parse_time(item.get("time"))
        if instant is None:
            reason = "缺少时间" if not item.get("time") else "时间无法解析"
            untimed.append({"record": record, "reason": reason})
            continue
        parsed.append({
            "record": record,
            "instant": instant,
            "assumed": assumed,
            "subject": item.get("subject", "") or "",
            "object": item.get("object", "") or "",
            "action": item.get("action", "") or "",
            "source": item.get("source", "unknown") or "unknown",
        })

    groups = {}
    for entry in parsed:
        groups.setdefault((entry["subject"], entry["object"], entry["action"]), []).append(entry)

    segments = []
    for members in groups.values():
        members.sort(key=lambda entry: entry["instant"])
        segment = [members[0]]
        for entry in members[1:]:
            if (entry["instant"] - segment[-1]["instant"]).total_seconds() <= merge_window:
                segment.append(entry)
            else:
                segments.append(segment)
                segment = [entry]
        segments.append(segment)

    ranked = sorted(
        ((segment[0]["instant"], _merge_segment(segment, skew_threshold)) for segment in segments),
        key=lambda pair: pair[0],
    )
    events = [event for _, event in ranked]
    conflicts = [
        {"subject": event["subject"], "object": event["object"], "action": event["action"],
         "skew": event["skew"], "entries": event["times"]}
        for event in events if event["conflict"]
    ]
    merged = sum(len(event["ids"]) - 1 for event in events)
    skew = max((event["skew"] for event in events), default=0.0)
    return events, untimed, conflicts, merged, skew


def timeline(evidence, case=None, merge_window=MERGE_WINDOW, skew_threshold=SKEW_THRESHOLD):
    """归一各来源时间到 UTC 并排序；相近事件合并保留全部来源，偏差超阈值的并列进 conflicts。"""
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence 不能为空")
    if case is not None:
        _CASES.setdefault(case, []).extend(evidence)
        evidence = _CASES[case]
    events, untimed, conflicts, merged, skew = _build(evidence, merge_window, skew_threshold)
    _LAST.clear()
    _LAST.update({"events": events, "untimed": untimed, "conflicts": conflicts,
                  "merged": merged, "skew": skew, "case": case})
    return {
        "events": [dict(event) for event in events],
        "untimed": [dict(item) for item in untimed],
        "count": len(events),
        "merged": merged,
        "sources": sorted({source for event in events for source in event["sources"]}),
        "conflicts": [dict(conflict) for conflict in conflicts],
        "skew": skew,
        "case": case,
    }


def report(case=None):
    """汇总最近一次（或指定案件的）时间线：事件数、来源分布、合并条数、冲突数、最大偏差。"""
    if case is not None:
        if case not in _CASES:
            raise ValueError("未知案件: %s" % case)
        events, untimed, conflicts, merged, skew = _build(_CASES[case], MERGE_WINDOW, SKEW_THRESHOLD)
    else:
        events, untimed, conflicts = _LAST["events"], _LAST["untimed"], _LAST["conflicts"]
        merged, skew, case = _LAST["merged"], _LAST["skew"], _LAST["case"]
    sources = {}
    for event in events:
        for source in event["sources"]:
            sources[source] = sources.get(source, 0) + 1
    return {
        "events": len(events),
        "sources": sources,
        "merged": merged,
        "conflicts": len(conflicts),
        "skew": skew,
        "untimed": len(untimed),
        "case": case,
    }


def by_source(source, case=None):
    """按来源筛出事件（含合并进该来源的事件），便于核对单份证据。"""
    if not isinstance(source, str) or not source:
        raise ValueError("source 不能为空")
    if case is not None:
        if case not in _CASES:
            raise ValueError("未知案件: %s" % case)
        events = _build(_CASES[case], MERGE_WINDOW, SKEW_THRESHOLD)[0]
    else:
        events = _LAST["events"]
    return [dict(event) for event in events if source in event["sources"]]
