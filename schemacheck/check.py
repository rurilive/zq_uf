"""入库前校验：可配置规则集、依赖跳过、错误率预算、流式校验与样本重放。

规则结构（全部为可选）::

    rules = {
        "dataset": "users",                 # 数据集名，用于按数据集匹配预算
        "required": ["id", "name"],         # 必填字段（空值视为缺失）
        "fields": {                          # 单字段规则简写，键即字段名
            "age": {"type": "int"},
            "name": {"length": {"min": 1, "max": 8}},
            "role": {"enum": ["admin", "user"]},
            "email": {"regex": r"^[^@]+@[^@]+$", "depends_on": ["age"]},
            "dept": {"reference": {"dataset": "depts", "key": "id"}},
        },
        "checks": [                          # 显式规则列表，可自定义 id
            {"id": "range", "cross_field": {"left": "start", "op": "<=", "right": "end"}},
        ],
        "datasets": {"depts": [{"id": "D1"}]},  # 跨数据集引用的参照数据
        "budget": 0.2,                       # 错误率预算；或 {"users": 0.2, "default": 0.0}
    }
"""

import operator
import re

_OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def _is_int(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        try:
            int(value)
        except ValueError:
            return False
        return True
    return False


def _is_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return True
    return False


_TYPE_CHECKS = {
    "str": lambda value: isinstance(value, str),
    "bool": lambda value: isinstance(value, bool),
    "int": _is_int,
    "float": _is_number,
    "number": _is_number,
    "list": lambda value: isinstance(value, list),
    "dict": lambda value: isinstance(value, dict),
}


def _check_type(value, type_name):
    try:
        predicate = _TYPE_CHECKS[type_name]
    except KeyError:
        raise ValueError("unknown type: %r" % (type_name,))
    return predicate(value)


def _compare(left, op, right):
    try:
        func = _OPERATORS[op]
    except KeyError:
        raise ValueError("unknown operator: %r" % (op,))
    try:
        return func(left, right)
    except TypeError:
        try:
            return func(float(left), float(right))
        except (TypeError, ValueError):
            return False


def _normalize_checks(rules):
    checks = []
    for field, spec in (rules.get("fields") or {}).items():
        check = dict(spec)
        check.setdefault("id", field)
        check["field"] = field
        checks.append(check)
    for index, check in enumerate(rules.get("checks") or []):
        check = dict(check)
        check.setdefault("id", "check_%d" % index)
        checks.append(check)
    return checks


def _dataset_values(datasets, spec, cache):
    name = spec["dataset"]
    key = spec.get("key")
    cache_key = (name, key)
    if cache_key not in cache:
        values = set()
        for item in datasets.get(name) or []:
            if key is not None and isinstance(item, dict):
                values.add(item.get(key))
            else:
                values.add(item)
        cache[cache_key] = values
    return cache[cache_key]


def _run_check(check, row, datasets, dataset_cache):
    """对单行执行一条规则，返回 (field, value, reason) 失败列表。"""
    failures = []
    if "cross_field" in check:
        spec = check["cross_field"]
        left = row.get(spec["left"])
        right = row.get(spec["right"])
        if not _compare(left, spec["op"], right):
            failures.append((
                spec["left"],
                left,
                "cross-field check failed: %r %s %r" % (left, spec["op"], right),
            ))
        return failures

    field = check["field"]
    value = row.get(field)

    if "type" in check and not _check_type(value, check["type"]):
        failures.append((field, value, "expected type %s" % check["type"]))

    if "length" in check:
        spec = check["length"]
        try:
            size = len(value)
        except TypeError:
            failures.append((field, value, "has no length"))
        else:
            if "min" in spec and size < spec["min"]:
                failures.append((field, value, "length %d < min %d" % (size, spec["min"])))
            if "max" in spec and size > spec["max"]:
                failures.append((field, value, "length %d > max %d" % (size, spec["max"])))

    if "enum" in check and value not in check["enum"]:
        failures.append((field, value, "not in enum %r" % (check["enum"],)))

    if "regex" in check:
        if not isinstance(value, str) or not re.fullmatch(check["regex"], value):
            failures.append((field, value, "does not match /%s/" % check["regex"]))

    if "reference" in check:
        spec = check["reference"]
        allowed = _dataset_values(datasets, spec, dataset_cache)
        if value not in allowed:
            failures.append((
                field,
                value,
                "not found in dataset %r" % spec["dataset"],
            ))

    return failures


def _iter_events(rows, rules):
    """逐行校验，产生 ("ok", row) / ("bad", entry) / ("skipped", entry) 事件。"""
    required = rules.get("required") or []
    checks = _normalize_checks(rules)
    datasets = rules.get("datasets") or {}
    dataset_cache = {}
    for index, row in enumerate(rows):
        missing = [name for name in required if not row.get(name)]
        if missing:
            yield ("bad", {
                "row": index,
                "missing": missing,
                "reason": "missing required fields",
                "data": row,
            })
            continue
        not_passed = set()
        failed = False
        for check in checks:
            broken = set(check.get("depends_on") or ()) & not_passed
            if broken:
                not_passed.add(check["id"])
                yield ("skipped", {
                    "row": index,
                    "check": check["id"],
                    "skipped": True,
                    "reason": "dependency failed: %s" % ", ".join(sorted(broken)),
                })
                continue
            failures = _run_check(check, row, datasets, dataset_cache)
            if failures:
                not_passed.add(check["id"])
                failed = True
                for field, value, reason in failures:
                    yield ("bad", {
                        "row": index,
                        "check": check["id"],
                        "field": field,
                        "value": value,
                        "reason": reason,
                        "data": row,
                    })
        if not failed:
            yield ("ok", row)


def _budget_for(rules):
    """返回 (dataset, max_error_rate)；未配置预算时 max_error_rate 为 None。"""
    dataset = rules.get("dataset", "default")
    budget = rules.get("budget")
    if budget is None:
        return dataset, None
    if isinstance(budget, dict):
        if "max_error_rate" in budget:
            return dataset, budget["max_error_rate"]
        return dataset, budget.get(dataset, budget.get("default"))
    return dataset, float(budget)


def validate(rows, rules):
    """校验一批记录，返回 {"ok": [...], "bad": [...]} 及可选的诊断键。

    bad 条目带行号、字段、原值、原因和原始记录（可直接重放）。
    配置 budget 后按数据集统计错误率，超预算时整批拒绝（ok 清空并置
    batch_rejected），未超预算时只隔离坏记录。
    """
    passed = []
    rejected = []
    skipped = []
    bad_rows = set()
    for kind, payload in _iter_events(rows, rules):
        if kind == "ok":
            passed.append(payload)
        elif kind == "bad":
            rejected.append(payload)
            bad_rows.add(payload["row"])
        else:
            skipped.append(payload)

    result = {"ok": passed, "bad": rejected}
    if skipped:
        result["skipped"] = skipped

    dataset, max_rate = _budget_for(rules)
    if max_rate is not None:
        total = len(passed) + len(bad_rows)
        rate = len(bad_rows) / total if total else 0.0
        exceeded = rate > max_rate
        result["budget"] = {
            "dataset": dataset,
            "error_rate": rate,
            "max_error_rate": max_rate,
            "bad_rows": len(bad_rows),
            "total_rows": total,
            "exceeded": exceeded,
        }
        if exceeded:
            result["batch_rejected"] = True
            result["ok"] = []
    return result


def validate_stream(rows, rules):
    """流式校验：逐条产生事件，不整体加载，结尾产出 ("summary", summary)。

    事件为 ("ok", row)、("bad", entry)、("skipped", entry)，
    最后一条为 ("summary", {...})，含错误率与预算判定。
    """
    total = 0
    bad_rows = 0
    seen_bad = set()
    for kind, payload in _iter_events(rows, rules):
        if kind == "ok":
            total += 1
        elif kind == "bad" and payload["row"] not in seen_bad:
            seen_bad.add(payload["row"])
            bad_rows += 1
            total += 1
        yield (kind, payload)
    dataset, max_rate = _budget_for(rules)
    summary = {"dataset": dataset, "total_rows": total, "bad_rows": bad_rows}
    if max_rate is not None:
        rate = bad_rows / total if total else 0.0
        summary.update({
            "error_rate": rate,
            "max_error_rate": max_rate,
            "exceeded": rate > max_rate,
        })
    yield ("summary", summary)


def replay(samples, rules):
    """重放拒绝样本：接受 validate 的 bad 列表（或原始行），重新校验。"""
    rows = []
    for sample in samples:
        if isinstance(sample, dict) and "data" in sample and "row" in sample:
            rows.append(sample["data"])
        else:
            rows.append(sample)
    return validate(rows, rules)

