"""规则配置：解析、校验、版本管理。

一条规则 = 条件（field/op）+ 阈值或取值 + 动作，全部来自配置，不在代码里写死。
条件与阈值分开：数字比较用 threshold，相等/集合比较用 value。
版本一经保存不会被覆盖，便于对比回放差异。

在条件层面可以证明互斥的规则（不同字段，或同字段上的区间/集合不相交）不会
被报告为冲突；其余同优先级组合会在执行前作为警告返回。
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone

NUMBER_OPS = (">=", ">", "<=", "<")
VALUE_OPS = ("==", "!=", "in", "not_in")
OPS = NUMBER_OPS + VALUE_OPS
ACTIONS = ("allow", "deny", "review")

DEFAULT_CONFIG = {
    "version": "v1",
    "rules": [
        {
            "id": "r1",
            "priority": 100,
            "condition": {"field": "amount", "op": ">="},
            "threshold": 1000,
            "action": "deny",
            "description": "大额交易",
        },
        {
            "id": "r2",
            "priority": 90,
            "condition": {"field": "country", "op": "=="},
            "value": "RU",
            "action": "deny",
            "description": "高风险国家",
        },
        {
            "id": "r3",
            "priority": 80,
            "condition": {"field": "channel", "op": "=="},
            "value": "web",
            "action": "review",
            "description": "网页渠道复核",
        },
    ],
}


class ConfigError(ValueError):
    """配置非法：缺字段、类型错误、版本重复等。"""


class NotFoundError(KeyError):
    """版本、规则或事件不存在。"""


_VERSIONS = {}
_ACTIVE = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _format_value(value):
    return json.dumps(value, ensure_ascii=False)


def condition_text(rule):
    """把条件渲染成可读文本，用于解释和日志。"""
    if rule["op"] in NUMBER_OPS:
        return "%s %s %s" % (rule["field"], rule["op"], _format_value(rule["threshold"]))
    return "%s %s %s" % (rule["field"], rule["op"], _format_value(rule["value"]))


def _compile_rule(raw, index):
    if not isinstance(raw, dict):
        raise ConfigError("rule #%d must be an object" % index)
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ConfigError("rule #%d missing non-empty id" % index)

    condition = raw.get("condition")
    if condition is None:
        field, op = raw.get("field"), raw.get("op")
    elif isinstance(condition, dict):
        field, op = condition.get("field"), condition.get("op")
    else:
        raise ConfigError("rule %s: condition must be an object" % rule_id)
    if not isinstance(field, str) or not field:
        raise ConfigError("rule %s: missing field" % rule_id)
    if op not in OPS:
        raise ConfigError(
            "rule %s: unsupported op %r (expected one of %s)" % (rule_id, op, ", ".join(OPS))
        )

    priority = raw.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ConfigError("rule %s: priority must be an integer" % rule_id)

    action = raw.get("action", "deny")
    if action not in ACTIONS:
        raise ConfigError(
            "rule %s: unsupported action %r (expected one of %s)"
            % (rule_id, action, ", ".join(ACTIONS))
        )

    rule = {
        "id": rule_id,
        "priority": priority,
        "field": field,
        "op": op,
        "action": action,
        "description": str(raw.get("description", "")),
        "index": index,
        "threshold": None,
        "value": None,
    }

    if op in NUMBER_OPS:
        threshold = raw.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ConfigError("rule %s: op %s requires a numeric threshold" % (rule_id, op))
        rule["threshold"] = threshold
    else:
        value = raw.get("value")
        if op in ("in", "not_in"):
            if not isinstance(value, (list, tuple, set)) or not value:
                raise ConfigError("rule %s: op %s requires a non-empty list value" % (rule_id, op))
            value = list(value)
        elif value is None:
            raise ConfigError("rule %s: op %s requires a value" % (rule_id, op))
        rule["value"] = value

    rule["condition"] = condition_text(rule)
    return rule


def _interval(rule):
    """把数字比较规则转成区间 (low, low_inclusive, high, high_inclusive)。"""
    t = rule["threshold"]
    inf = float("inf")
    if rule["op"] == ">=":
        return (t, True, inf, False)
    if rule["op"] == ">":
        return (t, False, inf, False)
    if rule["op"] == "<=":
        return (-inf, False, t, True)
    return (-inf, False, t, False)


def _intervals_overlap(a, b):
    low = max(a[0], b[0])
    if a[0] != b[0]:
        low_inclusive = a[1] if a[0] > b[0] else b[1]
    else:
        low_inclusive = a[1] and b[1]
    high = min(a[2], b[2])
    if a[2] != b[2]:
        high_inclusive = a[3] if a[2] < b[2] else b[3]
    else:
        high_inclusive = a[3] and b[3]
    if low != high:
        return low < high
    return low_inclusive and high_inclusive


def _positive_set(rule):
    if rule["op"] == "==":
        return [rule["value"]]
    return list(rule["value"])


def _negative_set(rule):
    if rule["op"] == "!=":
        return [rule["value"]]
    return list(rule["value"])


def _value_rules_overlap(a, b):
    a_positive = a["op"] in ("==", "in")
    b_positive = b["op"] in ("==", "in")
    if a_positive and b_positive:
        return any(item in _positive_set(b) for item in _positive_set(a))
    if a_positive:
        return any(item not in _negative_set(b) for item in _positive_set(a))
    if b_positive:
        return any(item not in _negative_set(a) for item in _positive_set(b))
    return True


def _rules_overlap(a, b):
    """两条规则的条件是否可能同时成立（用于发现优先级冲突）。"""
    if a["field"] != b["field"]:
        return False
    a_number = a["op"] in NUMBER_OPS
    b_number = b["op"] in NUMBER_OPS
    if a_number and b_number:
        return _intervals_overlap(_interval(a), _interval(b))
    if a_number != b_number:
        # 数字比较只对数字成立；等值/集合规则若只可能命中非数字则互斥
        value_rule = b if a_number else a
        if value_rule["op"] in ("==", "in"):
            return any(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in _positive_set(value_rule)
            )
        return True
    return _value_rules_overlap(a, b)


def detect_priority_conflicts(rules):
    """执行前检查：同优先级且条件可能同时命中的规则。"""
    warnings = []
    for i in range(len(rules)):
        for j in range(i + 1, len(rules)):
            a, b = rules[i], rules[j]
            if a["priority"] != b["priority"] or not _rules_overlap(a, b):
                continue
            warnings.append(
                {
                    "type": "priority_conflict",
                    "priority": a["priority"],
                    "rules": [a["id"], b["id"]],
                    "message": "规则 %s 与 %s 优先级同为 %d 且条件可能同时命中，"
                    "将按配置顺序取 %s 的结论" % (a["id"], b["id"], a["priority"], a["id"]),
                    "resolved_by": "config_order",
                }
            )
    return warnings


def compile_config(config, version=None):
    """校验并编译配置，返回内部结构：规则带 index 和可读条件。"""
    if isinstance(config, dict):
        raw_rules = config.get("rules")
        version_name = version or config.get("version")
    elif isinstance(config, (list, tuple)):
        raw_rules = list(config)
        version_name = version
    else:
        raise ConfigError("config must be an object or a list of rules")
    if not isinstance(raw_rules, list):
        raise ConfigError("config must contain a rules list")

    rules = []
    seen = set()
    for index, raw in enumerate(raw_rules):
        rule = _compile_rule(raw, index)
        if rule["id"] in seen:
            raise ConfigError("duplicate rule id: %s" % rule["id"])
        seen.add(rule["id"])
        rules.append(rule)

    return {
        "version": version_name,
        "rules": rules,
        "warnings": detect_priority_conflicts(rules),
        "created_at": _now(),
    }


def public_view(compiled):
    """可序列化的配置视图，隐去内部 index。"""
    view = copy.deepcopy(compiled)
    view["rules"] = [
        {key: value for key, value in rule.items() if key != "index"}
        for rule in compiled["rules"]
    ]
    view["active"] = compiled["version"] == _ACTIVE
    return view


def active_config():
    """当前生效的配置（含冲突提示）。"""
    return public_view(_VERSIONS[_ACTIVE])


def configure(config, version=None):
    """保存新版本并立即生效；旧版本保留，不允许覆盖。"""
    global _ACTIVE
    compiled = compile_config(config, version)
    name = compiled["version"] or _next_version_name()
    if name in _VERSIONS:
        raise ConfigError(
            "version %r already exists; rule changes keep old versions, use a new version name"
            % name
        )
    compiled["version"] = name
    _VERSIONS[name] = compiled
    _ACTIVE = name
    return active_config()


def load(path, version=None):
    """从 JSON 文件加载配置；未指定版本时用文件名作为版本号。"""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if version is None:
        version = os.path.splitext(os.path.basename(path))[0]
    return configure(data, version)


def activate(version):
    """切换到已保存的版本。"""
    global _ACTIVE
    if version not in _VERSIONS:
        raise NotFoundError("unknown rule version: %s" % version)
    _ACTIVE = version
    return active_config()


def history():
    """已保存的版本列表，旧版本不会被覆盖。"""
    return [
        {
            "version": name,
            "rules": len(compiled["rules"]),
            "created_at": compiled["created_at"],
            "active": name == _ACTIVE,
            "warnings": copy.deepcopy(compiled["warnings"]),
        }
        for name, compiled in _VERSIONS.items()
    ]


def get_compiled(spec=None):
    """按版本名、内联配置或 None（当前版本）取内部编译结果。"""
    if spec is None:
        return _VERSIONS[_ACTIVE]
    if isinstance(spec, str):
        if spec not in _VERSIONS:
            raise NotFoundError("unknown rule version: %s" % spec)
        return _VERSIONS[spec]
    if isinstance(spec, (dict, list, tuple)):
        compiled = compile_config(spec)
        compiled["version"] = compiled["version"] or "inline"
        return compiled
    raise ConfigError("config must be a version name, an object or a list of rules")


def _next_version_name():
    index = 1
    while "v%d" % index in _VERSIONS:
        index += 1
    return "v%d" % index


def reset_versions():
    """恢复默认配置，清空历史版本。"""
    global _VERSIONS, _ACTIVE
    _VERSIONS = {"v1": compile_config(DEFAULT_CONFIG, "v1")}
    _ACTIVE = "v1"


reset_versions()
