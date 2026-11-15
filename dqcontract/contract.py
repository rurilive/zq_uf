"""可配置的数据质量契约。

按表和字段声明非空、范围、枚举、唯一性规则；失败按严重程度隔离或告警；
规则版本随表结构登记，结构漂移给出提示；新规则可影子运行，确认后再阻断。
"""

import itertools

SEVERITY_BLOCK = "block"  # 隔离：行不入库
SEVERITY_WARN = "warn"    # 告警：行放行，只记录

MODE_ACTIVE = "active"  # 生效：失败即按级别处理
MODE_SHADOW = "shadow"  # 影子：只统计不处置，用于新规则灰度

RULE_TYPES = ("not_null", "range", "enum", "unique")

_STATUS_APPROVED = "approved"

_batch_seq = itertools.count(1)
_samples = {}  # batch_id -> 失败样本，供按批次回看


class ContractError(ValueError):
    """契约未审批、规则非法或与表结构冲突。"""


def _is_empty(value):
    return value is None or value == ""


def _failure(index, rule, mode, message, value=None):
    return {
        "row": index,
        "field": rule["field"],
        "rule": rule["type"],
        "severity": rule.get("severity", SEVERITY_BLOCK),
        "mode": mode,
        "message": message,
        "value": value,
    }


def _run_rule(rule, rows, mode):
    field, rtype = rule["field"], rule["type"]
    failures = []
    if rtype == "not_null":
        for i, row in enumerate(rows):
            if _is_empty(row.get(field)):
                failures.append(_failure(i, rule, mode, "值为空", row.get(field)))
    elif rtype == "range":
        lo, hi = rule.get("min"), rule.get("max")
        for i, row in enumerate(rows):
            v = row.get(field)
            if _is_empty(v):
                continue  # 空值交给 not_null
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                failures.append(_failure(i, rule, mode, "非数值，无法比较范围", v))
            elif (lo is not None and v < lo) or (hi is not None and v > hi):
                failures.append(_failure(i, rule, mode, "超出范围 [%s, %s]" % (lo, hi), v))
    elif rtype == "enum":
        allowed = set(rule["values"])
        for i, row in enumerate(rows):
            v = row.get(field)
            if not _is_empty(v) and v not in allowed:
                failures.append(_failure(i, rule, mode, "不在枚举值内", v))
    elif rtype == "unique":
        seen = {}
        for i, row in enumerate(rows):
            v = row.get(field)
            if _is_empty(v):
                continue
            if v in seen:
                failures.append(_failure(i, rule, mode, "与第 %s 行重复" % seen[v], v))
            else:
                seen[v] = i
    return failures


def drift_report(contract, schema):
    """对比契约与表结构，规则引用了已删除字段或字段无任何规则时给出提示。"""
    fields = set(schema.get("fields", []))
    drift = []
    for rule in contract.get("rules", []):
        if rule["field"] not in fields:
            drift.append({"kind": "rule_on_missing_field", "field": rule["field"],
                          "message": "规则引用的字段 %s 已不在表结构中" % rule["field"]})
    ruled = {r["field"] for r in contract.get("rules", [])}
    for field in sorted(fields - ruled):
        drift.append({"kind": "field_without_rule", "field": field,
                      "message": "字段 %s 没有任何质量规则" % field})
    return drift


def _validate_rules(contract, schema):
    for rule in contract.get("rules", []):
        if rule.get("type") not in RULE_TYPES:
            raise ContractError("未知规则类型: %r" % rule.get("type"))
        if not rule.get("field"):
            raise ContractError("规则缺少 field")
        if rule.get("severity", SEVERITY_BLOCK) not in (SEVERITY_BLOCK, SEVERITY_WARN):
            raise ContractError("未知严重程度: %r" % rule.get("severity"))
        if rule.get("mode", MODE_ACTIVE) not in (MODE_ACTIVE, MODE_SHADOW):
            raise ContractError("未知运行模式: %r" % rule.get("mode"))
        if rule["type"] == "enum" and not rule.get("values"):
            raise ContractError("枚举规则缺少 values")
        if rule["type"] == "range" and rule.get("min") is None and rule.get("max") is None:
            raise ContractError("范围规则至少需要 min 或 max")
    blocking = [d for d in drift_report(contract, schema) if d["kind"] == "rule_on_missing_field"]
    if blocking:
        raise ContractError("契约与表结构不同步: " + "; ".join(d["message"] for d in blocking))


def register_contract(contract, schema, approved_by=None):
    """登记契约：变更必须走审批，规则版本随表结构版本一起绑定。"""
    if not approved_by:
        raise ContractError("契约变更需要审批人")
    if not contract.get("version"):
        raise ContractError("契约缺少规则版本 version")
    _validate_rules(contract, schema)
    registered = dict(contract)
    registered["status"] = _STATUS_APPROVED
    registered["approved_by"] = approved_by
    registered["schema_version"] = schema.get("version")
    return registered


def check(batch, contract, schema=None):
    """按契约检查一批数据，返回通过、隔离、告警、影子统计和规则版本。"""
    if contract.get("status") != _STATUS_APPROVED:
        raise ContractError("契约未审批登记，请先 register_contract")
    rows = list(batch.get("rows", []))
    batch_id = batch.get("batch_id") or "batch-%d" % next(_batch_seq)

    active_failures, shadow_failures = [], []
    for rule in contract.get("rules", []):
        mode = rule.get("mode", MODE_ACTIVE)
        found = _run_rule(rule, rows, mode)
        (shadow_failures if mode == MODE_SHADOW else active_failures).extend(found)

    quarantined = sorted({f["row"] for f in active_failures if f["severity"] == SEVERITY_BLOCK})
    alerted = sorted({f["row"] for f in active_failures if f["severity"] == SEVERITY_WARN}
                     - set(quarantined))
    passed = sorted(set(range(len(rows))) - set(quarantined))

    would_isolate = {f["row"] for f in shadow_failures if f["severity"] == SEVERITY_BLOCK}
    shadow = {
        "failures": len(shadow_failures),
        "would_isolate": len(would_isolate),
        "isolate_ratio": (len(would_isolate) / len(rows)) if rows else 0.0,
    }

    _samples[batch_id] = active_failures + shadow_failures
    return {
        "ok": not quarantined,
        "batch_id": batch_id,
        "version": contract.get("version"),
        "schema_version": contract.get("schema_version"),
        "total": len(rows),
        "passed": passed,
        "quarantined": quarantined,
        "alerted": alerted,
        "failures": active_failures,
        "shadow": shadow,
        "drift": drift_report(contract, schema) if schema else [],
    }


def enforce(result):
    """执行处置：有隔离即阻断入库，告警只记录。"""
    quarantined = result.get("quarantined", [])
    return {
        "blocked": bool(quarantined),
        "isolated": quarantined,
        "alerted": result.get("alerted", []),
        "logged": bool(quarantined or result.get("alerted") or result.get("failures")),
    }


def recheck(rows, contract, schema=None):
    """隔离数据修复后重新检查，通过的行可重新入库。"""
    return check({"rows": list(rows)}, contract, schema=schema)


def failure_samples(batch_id):
    """按批次回看失败样本。"""
    return list(_samples.get(batch_id, []))
