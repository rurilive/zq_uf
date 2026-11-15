"""采集结果核对：字段级核对、坏行隔离、局部回退、分层抽样。

纯标准库。对外接口保持 verify(rows, schema) 和 rollback(batch)。

schema 支持的键：
  required    必填字段列表（缺失即隔离，reason="missing"）
  fields      {字段: {"type": "int|str|float|number|bool|list|dict",
                      "min": x, "max": y, "choices": [...]}}
  rules       跨字段规则 [{"name": ..., "left": 字段, "op": "<|<=|==|!=|>=|>",
                           "right": 字段} 或 {"left": 字段, "op": ..., "value": 常量}]
  version     规则版本，见 RULE_VERSIONS，默认 v2
  sample_rate 抽样比例（按站点和错误类型分层），默认 0.1
  seed        抽样随机种子，默认 0（可复现）
  batch       批次号，缺省自动生成
"""

import math
import random
import time

# 规则版本：v1 兼容旧口径（只查字段存在），v2 启用类型/范围/跨字段核对。
RULE_VERSIONS = {
    "v1": {"types": False, "ranges": False, "rules": False},
    "v2": {"types": True, "ranges": True, "rules": True},
}
DEFAULT_RULE_VERSION = "v2"

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}

_QUARANTINE = {}  # batch -> [{"index", "row", "trace", "status"}]
_AUDIT = []       # 回退审计记录
_BATCH_SEQ = [0]


def register_rule_version(name, flags):
    """注册新的规则版本，flags 含 types/ranges/rules 三个开关。"""
    RULE_VERSIONS[name] = dict(flags)


def _flags(schema):
    version = schema.get("version", DEFAULT_RULE_VERSION)
    if version not in RULE_VERSIONS:
        raise ValueError("未知规则版本: %r" % (version,))
    return RULE_VERSIONS[version]


def _next_batch():
    _BATCH_SEQ[0] += 1
    return "batch-%d" % _BATCH_SEQ[0]


def _type_ok(value, type_name):
    if type_name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name in ("float", "number"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "bool":
        return isinstance(value, bool)
    expected = {"str": str, "list": list, "dict": dict}.get(type_name)
    if expected is None:
        raise ValueError("未知类型: %r" % (type_name,))
    return isinstance(value, expected)


def _check_row(row, schema, flags):
    """返回 (failures, trace)。trace 记录每一项核对的结果。"""
    trace = []
    failures = []

    missing = [n for n in schema.get("required", []) if n not in row]
    trace.append({"check": "required", "ok": not missing,
                  "detail": "missing: %s" % missing if missing else "ok"})
    if missing:
        failures.append({"reason": "missing", "fields": missing})

    specs = schema.get("fields", {})
    if flags["types"]:
        for name, spec in specs.items():
            if "type" not in spec or name not in row:
                continue
            ok = _type_ok(row[name], spec["type"])
            trace.append({"check": "type", "field": name, "ok": ok,
                          "detail": "expect %s, got %s" % (spec["type"], type(row[name]).__name__)})
            if not ok:
                failures.append({"reason": "type", "field": name,
                                 "expected": spec["type"], "got": type(row[name]).__name__})
    if flags["ranges"]:
        for name, spec in specs.items():
            if name not in row:
                continue
            value = row[name]
            # 类型已不符的字段由 type 核对报告，范围核对跳过
            if "type" in spec and not _type_ok(value, spec["type"]):
                continue
            detail = None
            try:
                if "min" in spec and not (value >= spec["min"]):
                    detail = "%r < min %r" % (value, spec["min"])
                elif "max" in spec and not (value <= spec["max"]):
                    detail = "%r > max %r" % (value, spec["max"])
                elif "choices" in spec and value not in spec["choices"]:
                    detail = "%r not in %r" % (value, spec["choices"])
            except TypeError:
                detail = "%r 不可比较" % (value,)
            ok = detail is None
            if "min" in spec or "max" in spec or "choices" in spec:
                trace.append({"check": "range", "field": name, "ok": ok,
                              "detail": detail or "ok"})
            if not ok:
                failures.append({"reason": "range", "field": name, "detail": detail})
    if flags["rules"]:
        for rule in schema.get("rules", []):
            name = rule.get("name", "rule")
            left = row.get(rule["left"], None)
            has_left = rule["left"] in row
            if "right" in rule:
                right = row.get(rule["right"])
                has_right = rule["right"] in row
            else:
                right = rule.get("value")
                has_right = True
            if not (has_left and has_right):
                trace.append({"check": "rule", "rule": name, "ok": True,
                              "detail": "skipped: operand missing"})
                continue
            try:
                ok = _OPS[rule["op"]](left, right)
            except TypeError:
                ok = False
            trace.append({"check": "rule", "rule": name, "ok": ok,
                          "detail": "%r %s %r" % (left, rule["op"], right)})
            if not ok:
                failures.append({"reason": "rule", "rule": name,
                                 "detail": "%s%s%s 不成立" % (rule["left"], rule["op"],
                                                             rule.get("right", rule.get("value")))})
    return failures, trace


def _site(row):
    return row.get("site", "default")


def _sample(rows, passed, isolated, rate, rng):
    """分层抽样：通过行按站点分层，隔离行按错误类型分层，每层至少抽 1 条。"""
    if rate <= 0:
        return []
    strata = {}
    for i in passed:
        key = "passed|%s" % _site(rows[i])
        strata.setdefault(key, []).append(
            {"index": i, "stratum": key, "site": _site(rows[i]), "reason": None})
    for item in isolated:
        key = "isolated|%s" % item["reason"]
        strata.setdefault(key, []).append(
            {"index": item["index"], "stratum": key,
             "site": _site(rows[item["index"]]), "reason": item["reason"]})
    sampled = []
    for key in sorted(strata):
        members = strata[key]
        k = min(len(members), max(1, math.ceil(len(members) * rate)))
        sampled.extend(rng.sample(members, k))
    return sorted(sampled, key=lambda s: (s["stratum"], s["index"]))


def verify(rows, schema):
    """字段级核对：好行通过，坏行隔离（记录原因与核对轨迹），并分层抽样。"""
    schema = dict(schema or {})
    flags = _flags(schema)
    batch = schema.get("batch") or _next_batch()
    rate = float(schema.get("sample_rate", 0.1))
    rng = random.Random(schema.get("seed", 0))

    passed, isolated, quarantine = [], [], []
    for index, row in enumerate(rows):
        failures, trace = _check_row(row, schema, flags)
        if failures:
            first = failures[0]
            item = {"index": index, "reason": first["reason"], "failures": failures}
            if "fields" in first:
                item["fields"] = first["fields"]
            isolated.append(item)
            quarantine.append({"index": index, "row": dict(row),
                               "trace": trace, "status": "isolated"})
        else:
            passed.append(index)
    _QUARANTINE[batch] = quarantine
    return {"batch": batch, "rule_version": schema.get("version", DEFAULT_RULE_VERSION),
            "passed": passed, "isolated": isolated,
            "rolled_back": [], "sampled": _sample(rows, passed, isolated, rate, rng)}


def rollback(batch):
    """局部回退：只回退坏行，好行保留入库，并写审计。

    batch 可传 verify 的返回 dict 或批次号字符串。
    对没有核对记录的批次号保留旧口径：整批回退（"all"）。
    """
    if isinstance(batch, dict):
        batch_id = batch.get("batch")
        indices = [item["index"] for item in batch.get("isolated", [])]
        kept = list(batch.get("passed", []))
    else:
        batch_id = batch
        entries = _QUARANTINE.get(batch_id)
        if entries is None:
            return {"batch": batch_id, "rolled_back": "all"}
        indices = [e["index"] for e in entries if e["status"] == "isolated"]
        kept = [e["index"] for e in entries if e["status"] != "isolated"]
    for entry in _QUARANTINE.get(batch_id, []):
        if entry["index"] in indices:
            entry["status"] = "rolled_back"
    record = {"batch": batch_id, "rolled_back": list(indices),
              "kept": list(kept), "at": time.time()}
    _AUDIT.append(record)
    return {"batch": batch_id, "rolled_back": list(indices),
            "kept": list(kept), "audit": record}


def reverify(batch, schema=None, fixes=None):
    """对隔离区（含已回退）的行重新核对。

    fixes: {行号: {字段: 新值}}，先打补丁再核；核对轨迹追加保留。
    通过的行状态置为 "passed" 并返回在 released 中。
    """
    schema = dict(schema or {})
    flags = _flags(schema)
    entries = _QUARANTINE.get(batch, [])
    released = []
    for entry in entries:
        if entry["status"] not in ("isolated", "rolled_back"):
            continue
        if fixes and entry["index"] in fixes:
            entry["row"].update(fixes[entry["index"]])
        failures, trace = _check_row(entry["row"], schema, flags)
        entry["trace"] = entry["trace"] + trace
        if not failures:
            entry["status"] = "passed"
            released.append(entry["index"])
    remaining = [e["index"] for e in entries if e["status"] in ("isolated", "rolled_back")]
    return {"batch": batch, "released": released, "remaining": remaining}


def quarantine(batch):
    """查看某批次的隔离区：原始行、核对轨迹、当前状态。"""
    return [dict(e) for e in _QUARANTINE.get(batch, [])]


def audit_log():
    """回退审计记录。"""
    return [dict(r) for r in _AUDIT]
