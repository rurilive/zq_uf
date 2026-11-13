"""多环境配置漂移检测：指纹快照、四类差异、影响分级、敏感项脱敏。"""

import hashlib
import json

MASK = "***"

_SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

_KIND_SEVERITY = {
    "added": "low",
    "removed": "high",
    "changed": "medium",
    "type_changed": "high",
}


def _hash_value(value):
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _fingerprint(config, sensitive):
    """敏感项以哈希代入，生成可安全跨环境比较的指纹。"""
    canon = {
        key: {"sha256": _hash_value(config[key])} if key in sensitive else config[key]
        for key in sorted(config)
    }
    payload = json.dumps(canon, sort_keys=True, default=repr)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot(env, config=None, sensitive=None):
    """采集某环境的运行配置指纹；sensitive 声明的键只存哈希。"""
    config = dict(config or {})
    sensitive = frozenset(sensitive or ())
    return {
        "env": env,
        "config": config,
        "sensitive": sorted(sensitive),
        "hashes": {key: _hash_value(config[key]) for key in sensitive if key in config},
        "fingerprint": _fingerprint(config, sensitive),
    }


def _display(value, masked):
    return MASK if masked else value


def _suggestion(kind, key, env, entry):
    if kind == "added":
        return "确认是否为有意新增：有意则回写仓库基线，否则从 %s 移除该键" % env
    if kind == "removed":
        return "将该键补回 %s 配置，或确认下线后从仓库基线删除" % env
    if kind == "type_changed":
        return "统一该键类型（基线 %s / 环境 %s），避免解析错误" % (
            entry["from_type"], entry["to_type"])
    return "以基线为准回滚该键，或评审后把新值回写仓库基线"


def _entry(kind, key, from_value, to_value, masked, env):
    severity = _KIND_SEVERITY[kind]
    if masked and kind in ("changed", "removed", "type_changed"):
        severity = "critical"
    entry = {
        "key": key,
        "kind": kind,
        "from": _display(from_value, masked),
        "to": _display(to_value, masked),
        "severity": severity,
        "source": {"added": "target_only", "removed": "base_only"}.get(kind, "both"),
    }
    if kind == "type_changed":
        entry["from_type"] = type(from_value).__name__
        entry["to_type"] = type(to_value).__name__
    entry["suggestion"] = _suggestion(kind, key, env, entry)
    if masked:
        entry["suggestion"] += "；敏感项已脱敏，按密钥轮换流程核对"
    return entry


def diff(base, target):
    """比较两份快照，差异分 added/removed/changed/type_changed 四类并分级。"""
    base_cfg, target_cfg = base["config"], target["config"]
    sensitive = set(base.get("sensitive", ())) | set(target.get("sensitive", ()))
    env = target["env"]
    added, removed, changed, type_changed = [], [], [], []

    for key in sorted(target_cfg):
        if key not in base_cfg:
            added.append(_entry("added", key, None, target_cfg[key], key in sensitive, env))
    for key in sorted(base_cfg):
        if key not in target_cfg:
            removed.append(_entry("removed", key, base_cfg[key], None, key in sensitive, env))
    for key in sorted(base_cfg):
        if key not in target_cfg:
            continue
        old, new = base_cfg[key], target_cfg[key]
        masked = key in sensitive
        if type(old) is not type(new):
            type_changed.append(_entry("type_changed", key, old, new, masked, env))
        elif masked:
            if _hash_value(old) != _hash_value(new):
                changed.append(_entry("changed", key, old, new, True, env))
        elif old != new:
            changed.append(_entry("changed", key, old, new, False, env))

    entries = added + removed + changed + type_changed
    severity = "none"
    for entry in entries:
        if _SEVERITY_ORDER[entry["severity"]] > _SEVERITY_ORDER[severity]:
            severity = entry["severity"]

    alerts = [
        {
            "env": env,
            "key": entry["key"],
            "kind": entry["kind"],
            "severity": entry["severity"],
            "source": entry["source"],
            "suggestion": entry["suggestion"],
        }
        for entry in entries
    ]

    return {
        "env": env,
        "base_env": base["env"],
        "fingerprint_match": base.get("fingerprint") == target.get("fingerprint"),
        "added": added,
        "removed": removed,
        "changed": changed,
        "type_changed": type_changed,
        "severity": severity,
        "alerts": alerts,
    }


def merge_alerts(*reports):
    """合并重复漂移告警：同 env+key+kind 的告警合并计数，按严重度排序。"""
    merged = {}
    for report in reports:
        for alert in report["alerts"]:
            dedup_key = (alert["env"], alert["key"], alert["kind"])
            if dedup_key in merged:
                merged[dedup_key]["count"] += 1
            else:
                merged[dedup_key] = dict(alert, count=1)
    return sorted(
        merged.values(),
        key=lambda a: (-_SEVERITY_ORDER[a["severity"]], a["env"], a["key"]),
    )
