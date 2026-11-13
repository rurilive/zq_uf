"""可配置的数据质量规则引擎。

run(dataset, rules) 支持两种规则形式：

* 旧形式 {"required": [...]}：保持原有行为，返回 {"passed": ..., "failed": ...}。
* 新形式 {"rules": [...], "thresholds": {...}, "sample_size": n}：
  执行可配置规则并返回结构化报告。
"""

DEFAULT_SAMPLE_SIZE = 5
DEFAULT_THRESHOLDS = {"warning": 0.0, "error": 1.0}

_EXPRESSION_GLOBALS = {
    "__builtins__": {},
    "abs": abs,
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "str": str,
    "sum": sum,
}


def _check_not_null(row, rule, state):
    value = row.get(rule["field"])
    return value is not None and value != ""


def _check_unique(row, rule, state):
    value = row.get(rule["field"])
    key = repr(value)
    if key in state["seen"]:
        return False
    state["seen"].add(key)
    return True


def _check_range(row, rule, state):
    try:
        value = float(row.get(rule["field"]))
    except (TypeError, ValueError):
        return False
    if "min" in rule and value < rule["min"]:
        return False
    if "max" in rule and value > rule["max"]:
        return False
    return True


def _check_enum(row, rule, state):
    return row.get(rule["field"]) in rule["allowed"]


def _check_expression(row, rule, state):
    try:
        return bool(eval(rule["expression"], _EXPRESSION_GLOBALS, {"row": row}))
    except Exception:
        return False


_CHECKERS = {
    "not_null": _check_not_null,
    "unique": _check_unique,
    "range": _check_range,
    "enum": _check_enum,
    "expression": _check_expression,
}


def _execute_rule(dataset, rule, sample_size):
    checker = _CHECKERS[rule["type"]]
    state = {"seen": set()}
    passed = 0
    sample_rows = []
    for index, row in enumerate(dataset):
        if checker(row, rule, state):
            passed += 1
        elif len(sample_rows) < sample_size:
            sample_rows.append(index)
    violated = len(dataset) - passed
    return {
        "name": rule["name"],
        "type": rule["type"],
        "status": "passed" if violated == 0 else "failed",
        "passed": passed,
        "violated": violated,
        "sample_rows": sample_rows,
    }


def _run_engine(dataset, config):
    sample_size = config.get("sample_size", DEFAULT_SAMPLE_SIZE)
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(config.get("thresholds", {}))

    results = []
    by_name = {}
    for rule in config.get("rules", []):
        failed_deps = [
            dep
            for dep in rule.get("depends_on", [])
            if by_name.get(dep, {}).get("status") != "passed"
        ]
        if failed_deps:
            result = {
                "name": rule["name"],
                "type": rule["type"],
                "status": "skipped",
                "passed": 0,
                "violated": 0,
                "sample_rows": [],
                "skip_reason": "dependency failed: " + ", ".join(failed_deps),
            }
        else:
            result = _execute_rule(dataset, rule, sample_size)
        results.append(result)
        by_name[result["name"]] = result

    executed = [r for r in results if r["status"] != "skipped"]
    checks = sum(r["passed"] + r["violated"] for r in executed)
    violations = sum(r["violated"] for r in executed)
    rate = violations / checks if checks else 0.0
    if rate >= thresholds["error"]:
        level = "error"
    elif rate >= thresholds["warning"]:
        level = "warning"
    else:
        level = "ok"

    return {
        "total_rows": len(dataset),
        "rules": results,
        "checks": checks,
        "violations": violations,
        "violation_rate": rate,
        "level": level,
        "thresholds": thresholds,
    }


def _run_legacy(dataset, rules):
    required = rules.get("required", [])
    passed = []
    failed = []
    for index, row in enumerate(dataset):
        missing = [name for name in required if not row.get(name)]
        if missing:
            failed.append({"row": index, "missing": missing})
        else:
            passed.append(row)
    return {"passed": passed, "failed": failed}


def run(dataset, rules):
    if isinstance(rules, dict) and "rules" in rules:
        return _run_engine(dataset, rules)
    return _run_legacy(dataset, rules)
