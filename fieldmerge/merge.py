"""多来源按行一致合并。

每个来源一次性提交同一行的一组字段，合并时以来源为单位整组打分、
整组胜出，避免把不同来源的字段拼成自相矛盾的记录。

打分口径：权重 = 可信度 × 时间衰减系数，
衰减系数 = 0.5 ** (年龄 / 半衰期)，年龄按本行最大时间戳相对计算。
"""

META_CONFIDENCE = "$confidence"
META_TIMESTAMP = "$timestamp"
DEFAULT_HALF_LIFE = 3600.0
DEFAULT_CONFIDENCE = 1.0

_rows = {}
_sequence = 0
_half_life = DEFAULT_HALF_LIFE
_default_confidence = DEFAULT_CONFIDENCE


def configure(half_life=DEFAULT_HALF_LIFE, default_confidence=DEFAULT_CONFIDENCE):
    """配置衰减半衰期（秒）与缺省可信度。"""
    global _half_life, _default_confidence
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    _half_life = float(half_life)
    _default_confidence = float(default_confidence)


def reset():
    """清空所有已合并的行状态。"""
    global _sequence
    _rows.clear()
    _sequence = 0


class MergeResult(dict):
    """主记录字段（dict 键）加上可解释的合并过程信息。"""

    def __init__(self, main, *, winning_source, weight, sources,
                 candidates, conflicts, rules, has_conflict):
        super().__init__(main)
        self.winning_source = winning_source
        self.weight = weight
        self.sources = sources
        self.candidates = candidates
        self.conflicts = conflicts
        self.rules = rules
        self.has_conflict = has_conflict


def _parse_payload(payload):
    """拆出来源元信息与字段组，兼容旧的纯字段写法。"""
    if not isinstance(payload, dict):
        raise TypeError("source payload must be a dict: %r" % (payload,))
    structured = (
        META_CONFIDENCE in payload
        or META_TIMESTAMP in payload
        or "fields" in payload
    )
    if not structured:
        return _default_confidence, None, dict(payload)

    confidence = float(payload.get(META_CONFIDENCE, _default_confidence))
    timestamp = payload.get(META_TIMESTAMP)
    if "fields" in payload:
        fields = payload["fields"]
        if not isinstance(fields, dict):
            raise TypeError("fields must be a dict")
        fields = dict(fields)
    else:
        fields = {
            key: value
            for key, value in payload.items()
            if key not in (META_CONFIDENCE, META_TIMESTAMP)
        }
    return confidence, timestamp, fields


def _same_value(left, right):
    try:
        return bool(left == right)
    except Exception:
        return False


def _evaluate(row):
    """对整行的所有来源打分并选出主来源，产出候选、冲突与规则命中。"""
    rules = []
    if not row:
        return MergeResult(
            {}, winning_source=None, weight=None, sources={},
            candidates={}, conflicts=[], rules=[], has_conflict=False,
        )

    timestamps = [
        entry["timestamp"]
        for entry in row.values()
        if entry["timestamp"] is not None
    ]
    reference_time = max(timestamps) if timestamps else None

    sources = {}
    for source, entry in row.items():
        confidence = max(entry["confidence"], 0.0)
        timestamp = entry["timestamp"]
        if timestamp is None or reference_time is None:
            age, decay = None, 1.0
        else:
            age = max(reference_time - timestamp, 0.0)
            decay = 0.5 ** (age / _half_life)
        weight = confidence * decay
        sources[source] = {
            "confidence": confidence,
            "timestamp": timestamp,
            "decay": decay,
            "weight": weight,
            "fields": dict(entry["fields"]),
        }
        rules.append({
            "rule": "source_scored",
            "source": source,
            "confidence": confidence,
            "timestamp": timestamp,
            "decay": decay,
            "weight": weight,
        })
        if age:
            rules.append({
                "rule": "time_decay",
                "source": source,
                "age": age,
                "half_life": _half_life,
                "decay": decay,
            })

    ranked = sorted(
        sources,
        key=lambda name: (sources[name]["weight"], row[name]["sequence"]),
    )
    winning_source = ranked[-1]
    winner_weight = sources[winning_source]["weight"]
    tied = [name for name in row if sources[name]["weight"] == winner_weight]

    conflicts = []
    if len(tied) > 1:
        conflicts.append({
            "type": "source_tie",
            "sources": tied,
            "winner": winning_source,
            "reason": "same_weight_latest_wins",
        })
        rules.append({
            "rule": "tie_conflict",
            "sources": list(tied),
            "winner": winning_source,
        })
    rules.append({
        "rule": "winner_selected",
        "source": winning_source,
        "weight": winner_weight,
        "reason": "same_weight_latest_wins" if len(tied) > 1 else "max_weight",
    })

    all_fields = []
    for entry in row.values():
        for field in entry["fields"]:
            if field not in all_fields:
                all_fields.append(field)

    candidates = {}
    main = {}
    for field in all_fields:
        field_candidates = []
        for source, entry in row.items():
            if field not in entry["fields"]:
                continue
            info = sources[source]
            field_candidates.append({
                "source": source,
                "value": entry["fields"][field],
                "confidence": info["confidence"],
                "timestamp": info["timestamp"],
                "weight": info["weight"],
            })
        candidates[field] = field_candidates

        owned = [item for item in field_candidates
                 if item["source"] == winning_source]
        if owned:
            chosen, reason = owned[0], "winning_source"
        else:
            chosen = sorted(
                field_candidates,
                key=lambda item: (item["weight"], row[item["source"]]["sequence"]),
            )[-1]
            reason = "highest_weight"
        main[field] = chosen["value"]

        distinct = []
        for item in field_candidates:
            if not any(_same_value(item["value"], old) for old in distinct):
                distinct.append(item["value"])
        if len(distinct) > 1:
            conflicts.append({
                "type": "field_conflict",
                "field": field,
                "sources": [item["source"] for item in field_candidates],
                "values": {item["source"]: item["value"]
                           for item in field_candidates},
                "winner_source": chosen["source"],
                "winner_value": chosen["value"],
                "reason": reason,
            })
            rules.append({
                "rule": "field_conflict",
                "field": field,
                "winner_source": chosen["source"],
                "reason": reason,
            })

    return MergeResult(
        main,
        winning_source=winning_source,
        weight=winner_weight,
        sources=sources,
        candidates=candidates,
        conflicts=conflicts,
        rules=rules,
        has_conflict=bool(conflicts),
    )


def merge(row_id, values):
    """按来源聚合字段组，整组打分后返回主记录与解释信息。"""
    global _sequence
    row = _rows.setdefault(row_id, {})
    for source, payload in values.items():
        confidence, timestamp, fields = _parse_payload(payload)
        _sequence += 1
        row[source] = {
            "confidence": confidence,
            "timestamp": timestamp,
            "fields": fields,
            "sequence": _sequence,
        }
    return _evaluate(row)
