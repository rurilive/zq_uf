"""规则执行：评估、解释、回放与版本对比。

evaluate(event) 返回结论、命中的全部规则以及每个命中的取值。
多条规则同时命中时按 priority 从高到低取结论，priority 相同按配置顺序；
配置阶段和执行阶段都会报告优先级冲突。

explain(id) 可以用事件 id 查单次评估的完整取值和重放结果，也可以用规则 id
查规则条件及其在历史事件上的命中分布。

replay()/compare() 在历史事件上回放配置，支持按时间段圈定范围，给出通过率
和命中分布，便于调整阈值前先跑历史数据对比。
"""

from __future__ import annotations

import copy

from . import config as rule_config
from . import store

__all__ = [
    "compare",
    "evaluate",
    "explain",
    "replay",
    "reset",
    "validate",
]

_NUMBER_TYPES = (int, float)


def _is_number(value):
    return isinstance(value, _NUMBER_TYPES) and not isinstance(value, bool)


def _eq(actual, expected):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(actual, bool) == isinstance(expected, bool) and actual == expected
    return actual == expected


def _match_rule(rule, event):
    """返回 (是否命中, 实际取值)。字段缺失时不命中任何规则。"""
    field = rule["field"]
    if field not in event:
        return False, None
    actual = event[field]
    op = rule["op"]
    if op in rule_config.NUMBER_OPS:
        if not _is_number(actual):
            return False, actual
        threshold = rule["threshold"]
        ok = {
            ">=": actual >= threshold,
            ">": actual > threshold,
            "<=": actual <= threshold,
            "<": actual < threshold,
        }[op]
        return ok, actual
    if op == "==":
        return _eq(actual, rule["value"]), actual
    if op == "!=":
        return not _eq(actual, rule["value"]), actual
    if op == "in":
        return any(_eq(actual, item) for item in rule["value"]), actual
    if op == "not_in":
        return not any(_eq(actual, item) for item in rule["value"]), actual
    raise rule_config.ConfigError("unsupported op: %s" % op)


def _evaluate_compiled(snapshot, compiled):
    """在给定配置下评估事件快照，返回结论、全部命中与冲突。"""
    matches = []
    for rule in compiled["rules"]:
        hit, actual = _match_rule(rule, snapshot)
        if hit:
            matches.append(
                {
                    "rule": rule["id"],
                    "action": rule["action"],
                    "priority": rule["priority"],
                    "condition": rule["condition"],
                    "field": rule["field"],
                    "actual": copy.deepcopy(actual),
                    "index": rule["index"],
                }
            )
    matches.sort(key=lambda item: (-item["priority"], item["index"]))

    conflicts = []
    if len(matches) > 1:
        top_priority = matches[0]["priority"]
        tied = [item["rule"] for item in matches if item["priority"] == top_priority]
        if len(tied) > 1:
            conflicts.append(
                {
                    "type": "priority_conflict",
                    "priority": top_priority,
                    "rules": tied,
                    "message": "规则 %s 优先级同为 %d 且同时命中，按配置顺序取 %s 的结论"
                    % ("、".join(tied), top_priority, tied[0]),
                    "resolved_by": "config_order",
                }
            )

    values = {}
    for item in matches:
        values[item["field"]] = copy.deepcopy(item["actual"])

    if matches:
        action, rule_id = matches[0]["action"], matches[0]["rule"]
    else:
        action, rule_id = "allow", ""

    for item in matches:
        item.pop("index")

    warnings = []
    seen = set()
    for warning in conflicts + copy.deepcopy(compiled["warnings"]):
        key = (warning["type"], warning["priority"], tuple(warning["rules"]))
        if key not in seen:
            seen.add(key)
            warnings.append(warning)

    return {
        "event": snapshot.get("id", ""),
        "version": compiled["version"],
        "action": action,
        "rule": rule_id,
        "values": values,
        "matched": matches,
        "conflicts": conflicts,
        "warnings": warnings,
    }


def validate(config=None):
    """只校验配置并报告优先级冲突，不生效、不写历史。"""
    compiled = rule_config.get_compiled(config)
    return copy.deepcopy(compiled["warnings"])


def evaluate(event, config=None, record=True):
    """评估事件，返回结论、命中规则和取值；默认保留完整取值供回放复核。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    snapshot = copy.deepcopy(event)
    compiled = rule_config.get_compiled(config)
    result = _evaluate_compiled(snapshot, compiled)
    if record:
        store.add(result["event"], snapshot, compiled["version"], result)
    return result


def _distribution(rows):
    counts = {action: 0 for action in rule_config.ACTIONS}
    hits = {}
    decisive = {}
    conflicts = []
    seen = set()
    for row in rows:
        counts[row["action"]] += 1
        for item in row["matched"]:
            hits[item["rule"]] = hits.get(item["rule"], 0) + 1
        if row["rule"]:
            decisive[row["rule"]] = decisive.get(row["rule"], 0) + 1
        for conflict in row["conflicts"]:
            key = (conflict["type"], conflict["priority"], tuple(conflict["rules"]))
            if key not in seen:
                seen.add(key)
                conflicts.append(copy.deepcopy(conflict))
    total = len(rows)
    return {
        "events": total,
        "actions": counts,
        "pass_rate": (counts["allow"] / total) if total else 0.0,
        "hits": hits,
        "decisive": decisive,
        "conflicts": conflicts,
    }


def _rows_for(compiled, entries):
    return [_evaluate_compiled(snapshot, compiled) for snapshot, _ in entries]


def _entries(events, start, end):
    if events is not None:
        if not isinstance(events, (list, tuple)):
            raise ValueError("events must be a list of event dicts")
        return [(copy.deepcopy(event), None) for event in events]
    return [
        (record["snapshot"], record["epoch"])
        for record in store.records(start, end)
    ]


def _bound(value):
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def replay(config=None, start=None, end=None, events=None):
    """在历史事件上回放规则配置。

    config 可以是版本名或内联配置；默认当前生效版本。
    start/end 限定记录时间段（时间戳、datetime、date 或 ISO 字符串）。
    events 直接给事件列表时只做试算，不依赖已记录数据。
    """
    compiled = rule_config.get_compiled(config)
    entries = _entries(events, start, end)
    rows = _rows_for(compiled, entries)
    summary = _distribution(rows)
    summary["version"] = compiled["version"]
    summary["range"] = {"start": _bound(start), "end": _bound(end)}
    summary["rule_order"] = [rule["id"] for rule in compiled["rules"]]
    summary["by_event"] = [
        {
            "event": row["event"],
            "action": row["action"],
            "rule": row["rule"],
            "matched": [item["rule"] for item in row["matched"]],
        }
        for row in rows
    ]
    return summary


def _explain_rule(rule_id, config, start, end):
    compiled = rule_config.get_compiled(config)
    target = next((rule for rule in compiled["rules"] if rule["id"] == rule_id), None)
    if target is None:
        raise rule_config.NotFoundError("unknown rule: %s" % rule_id)
    entries = _entries(None, start, end)
    rows = _rows_for(compiled, entries)

    hit_events = []
    decisive_events = []
    for row in rows:
        if any(item["rule"] == rule_id for item in row["matched"]):
            hit_events.append({"event": row["event"], "action": row["action"], "rule": row["rule"]})
        if row["rule"] == rule_id:
            decisive_events.append({"event": row["event"], "action": row["action"]})

    summary = _distribution(rows)
    summary["version"] = compiled["version"]
    summary["rule"] = rule_id
    summary["rule_hits"] = summary["hits"].get(rule_id, 0)
    summary["rule_decisive"] = summary["decisive"].get(rule_id, 0)

    return {
        "type": "rule",
        "rule": rule_id,
        "version": compiled["version"],
        "condition": target["condition"],
        "field": target["field"],
        "op": target["op"],
        "threshold": target["threshold"],
        "value": target["value"],
        "action": target["action"],
        "priority": target["priority"],
        "description": target["description"],
        "definition": {key: copy.deepcopy(value) for key, value in target.items() if key != "index"},
        "replay": summary,
        "events": hit_events,
        "decisive_events": decisive_events,
    }


def _explain_event(event_id, config):
    record = store.get(event_id)
    if record is None:
        raise rule_config.NotFoundError("unknown event: %s" % event_id)
    compiled = rule_config.get_compiled(config)
    current = _evaluate_compiled(record["snapshot"], compiled)

    matches = []
    for rule in compiled["rules"]:
        hit, actual = _match_rule(rule, record["snapshot"])
        matches.append(
            {
                "rule": rule["id"],
                "matched": hit,
                "actual": copy.deepcopy(actual),
                "condition": rule["condition"],
                "priority": rule["priority"],
                "action": rule["action"],
                "decisive": hit and rule["id"] == current["rule"],
            }
        )

    summary = _distribution([current])
    summary["version"] = compiled["version"]

    return {
        "type": "event",
        "event": event_id,
        "version": compiled["version"],
        "action": current["action"],
        "rule": current["rule"],
        "values": current["values"],
        "matched": current["matched"],
        "snapshot": copy.deepcopy(record["snapshot"]),
        "recorded": {
            "version": record["version"],
            "action": record["action"],
            "rule": record["rule"],
            "values": copy.deepcopy(record["values"]),
            "matched": copy.deepcopy(record["matched"]),
            "at": record["at"],
        },
        "changed": record["action"] != current["action"] or record["rule"] != current["rule"],
        "matches": matches,
        "replay": summary,
        "conflicts": current["conflicts"],
    }


def explain(identifier, config=None, start=None, end=None, kind=None):
    """解释一次命中或一条规则。

    identifier 为规则 id（如 r1）时返回规则条件和命中分布；
    为事件 id（如 e1）时返回该事件的结论、全部规则取值和重放结果。
    kind 可显式指定 "rule" 或 "event"，用于两者重名时消歧。
    """
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("identifier must be a non-empty string")
    if kind not in (None, "rule", "event"):
        raise ValueError('kind must be "rule" or "event"')
    compiled = rule_config.get_compiled(config)
    rule_ids = {rule["id"] for rule in compiled["rules"]}
    if kind == "rule" or (kind is None and identifier in rule_ids):
        return _explain_rule(identifier, config, start, end)
    if kind == "event" or (kind is None and store.get(identifier) is not None):
        return _explain_event(identifier, config)
    raise rule_config.NotFoundError("unknown rule or event: %s" % identifier)


def compare(from_config, to_config=None, start=None, end=None, events=None, limit=100):
    """对比两个版本在同一批事件上的通过率、命中分布与被改判事件。

    to_config 省略时与当前生效版本比较。
    """
    from_compiled = rule_config.get_compiled(from_config)
    to_compiled = rule_config.get_compiled(to_config)
    entries = _entries(events, start, end)
    rows_from = _rows_for(from_compiled, entries)
    rows_to = _rows_for(to_compiled, entries)
    before = _distribution(rows_from)
    after = _distribution(rows_to)

    changes = []
    for old, new in zip(rows_from, rows_to):
        if old["action"] != new["action"] or old["rule"] != new["rule"]:
            changes.append(
                {
                    "event": new["event"],
                    "from_action": old["action"],
                    "to_action": new["action"],
                    "from_rule": old["rule"],
                    "to_rule": new["rule"],
                }
            )

    return {
        "from": from_compiled["version"],
        "to": to_compiled["version"],
        "events": len(entries),
        "from_replay": before,
        "to_replay": after,
        "pass_rate": {
            "from": before["pass_rate"],
            "to": after["pass_rate"],
            "delta": after["pass_rate"] - before["pass_rate"],
        },
        "actions": {"from": before["actions"], "to": after["actions"]},
        "hits": {"from": before["hits"], "to": after["hits"]},
        "changed_count": len(changes),
        "changed": changes[:limit],
        "truncated": len(changes) > limit,
        "warnings": {
            "from": copy.deepcopy(from_compiled["warnings"]),
            "to": copy.deepcopy(to_compiled["warnings"]),
        },
    }


def reset():
    """清空事件记录和版本历史，恢复默认配置。"""
    events = store.clear()["events"]
    rule_config.reset_versions()
    return {"events": events, "versions": 1, "active": rule_config.active_config()["version"]}
