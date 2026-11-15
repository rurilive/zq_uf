"""采集响应模式校验与降级。

校验按类型、枚举、必填和嵌套路径进行，错误携带完整路径
（根节点为 ``$``，对象字段用 ``.name``，数组元素用 ``[i]``）。

降级按字段重要性分级：

- ``critical``  ：不可修复，整条记录隔离；
- ``important`` ：用默认值替换（schema.default -> enum 首值 -> 类型默认值）；
- ``optional``  ：声明了 ``default`` 的按默认值替换，否则丢弃该字段。

修复后的字段在 ``degraded`` / ``provenance`` 中标记来源。
"""

import copy

_LEVELS = ("critical", "important", "optional")

_TYPE_DEFAULTS = {
    "string": "",
    "integer": 0,
    "number": 0.0,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}

# 特殊标记：表示该节点在降级时被丢弃。
_DROP = object()


def validate(payload, schema):
    """按路径校验 payload，并给出降级 / 隔离结果。

    返回字典包含 ``ok``、``status``、``errors``、``degraded``、
    ``isolated``、``provenance``、``path`` 和 ``payload``。
    """
    schema = schema or {}
    errors = []
    degraded = []
    working = copy.deepcopy(payload)
    result = _process(working, schema, "$", _root_importance(schema), errors, degraded)
    working = None if result is _DROP else result

    critical = [err for err in errors if err["importance"] == "critical"]
    if critical:
        status = "isolated"
        isolated = [
            {
                "path": err["path"],
                "reason": err["message"],
                "importance": err["importance"],
            }
            for err in critical
        ]
        payload_out = None
        degraded = []
        provenance = []
    elif errors:
        status = "degraded"
        isolated = []
        payload_out = working
        provenance = [{"path": item["path"], "source": item["source"]} for item in degraded]
    else:
        status = "pass"
        isolated = []
        payload_out = working
        provenance = []

    return {
        "ok": status == "pass",
        "status": status,
        "errors": errors,
        "degraded": degraded,
        "isolated": isolated,
        "provenance": provenance,
        "path": errors[0]["path"] if errors else None,
        "payload": payload_out,
    }


def repair(payload, schema=None):
    """按 schema 降级修复 payload。

    单参数调用保持原有无 schema 的 no-op 行为；传入 schema 时才应用默认值。
    """
    if schema is None:
        return {
            "payload": dict(payload),
            "repaired": False,
            "fields": [],
            "status": "pass",
            "isolated": [],
            "provenance": [],
        }

    report = validate(payload, schema)
    return {
        "payload": report["payload"],
        "repaired": bool(report["degraded"]),
        "fields": report["degraded"],
        "status": report["status"],
        "isolated": report["isolated"],
        "provenance": report["provenance"],
    }


def _process(value, schema, path, importance, errors, degraded):
    """校验并就地降级 value；返回降级后的值或 _DROP。"""
    expected = schema.get("type")
    if expected is not None and not _type_ok(value, expected):
        errors.append(
            _error(
                path,
                "type",
                "expected %s, got %s" % (_type_label(expected), _type_name(value)),
                expected,
                _type_name(value),
                importance,
            )
        )
        return _degrade(schema, path, importance, degraded, value)

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(
            _error(
                path,
                "enum",
                "value %r not in %r" % (value, list(enum)),
                list(enum),
                value,
                importance,
            )
        )
        return _degrade(schema, path, importance, degraded, value)

    if isinstance(value, dict):
        _process_object(value, schema, path, errors, degraded)
    elif isinstance(value, list):
        _process_array(value, schema, path, importance, errors, degraded)

    return value


def _process_object(value, schema, path, errors, degraded):
    required = list(schema.get("required", []))
    properties = schema.get("properties", {}) or {}

    filled = set()
    for name in required:
        if name in value:
            continue
        prop_schema = properties.get(name, {}) or {}
        child_path = _child_path(path, name)
        child_importance = _property_importance(prop_schema, name, required)
        errors.append(
            _error(
                child_path,
                "missing",
                "required field is missing",
                None,
                None,
                child_importance,
            )
        )
        if child_importance == "important":
            default, source = _default_for(prop_schema)
            value[name] = default
            filled.add(name)
            degraded.append(_action(child_path, "replace", default, source, child_importance))

    for name, prop_schema in properties.items():
        if name not in value or name in filled:
            continue
        prop_schema = prop_schema or {}
        child_path = _child_path(path, name)
        child_importance = _property_importance(prop_schema, name, required)
        repaired = _process(
            value[name], prop_schema, child_path, child_importance, errors, degraded
        )
        if repaired is _DROP:
            del value[name]
        else:
            value[name] = repaired


def _process_array(value, schema, path, importance, errors, degraded):
    items = schema.get("items")
    if items is None:
        return
    kept = []
    for index, item in enumerate(value):
        item_path = "%s[%d]" % (path, index)
        repaired = _process(item, items, item_path, importance, errors, degraded)
        if repaired is not _DROP:
            kept.append(repaired)
    value[:] = kept


def _degrade(schema, path, importance, degraded, value):
    """按重要性决定替换 / 丢弃 / 不处理。"""
    if importance == "important":
        default, source = _default_for(schema)
        degraded.append(_action(path, "replace", default, source, importance))
        return default
    if importance == "optional":
        if "default" in schema:
            default = copy.deepcopy(schema["default"])
            degraded.append(_action(path, "replace", default, "schema_default", importance))
            return default
        degraded.append(_action(path, "drop", None, "dropped", importance))
        return _DROP
    return value


def _property_importance(prop_schema, name, required):
    level = prop_schema.get("importance")
    if level in _LEVELS:
        # required 字段不能静默缺失：显式 optional 也按 important 处理。
        if level == "optional" and name in required:
            return "important"
        return level
    return "critical" if name in required else "optional"


def _root_importance(schema):
    level = schema.get("importance")
    return level if level in _LEVELS else "critical"


def _default_for(schema):
    """返回 (默认值, 来源)。"""
    if "default" in schema:
        return copy.deepcopy(schema["default"]), "schema_default"
    enum = schema.get("enum")
    if enum:
        return copy.deepcopy(enum[0]), "enum_first"
    expected = schema.get("type")
    if isinstance(expected, (list, tuple)):
        expected = expected[0] if expected else None
    if expected in _TYPE_DEFAULTS:
        return copy.deepcopy(_TYPE_DEFAULTS[expected]), "type_default"
    return None, "none"


def _type_ok(value, expected):
    if isinstance(expected, (list, tuple)):
        return any(_type_ok(value, item) for item in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True  # 未知类型不校验


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_label(expected):
    if isinstance(expected, (list, tuple)):
        return " | ".join(str(item) for item in expected)
    return str(expected)


def _child_path(path, name):
    return "%s.%s" % (path, name)


def _error(path, code, message, expected, actual, importance):
    return {
        "path": path,
        "code": code,
        "message": message,
        "expected": expected,
        "actual": actual,
        "importance": importance,
    }


def _action(path, action, value, source, importance):
    return {
        "path": path,
        "action": action,
        "value": value,
        "source": source,
        "importance": importance,
    }
