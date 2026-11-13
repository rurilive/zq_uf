"""业务表格导入：按 schema 解析、校验并分批提交。

schema 为列名列表时保持原行为：返回字符串字典列表，缺列补空串。
schema 为字典时走校验流程，返回导入报告，字段见 README。
"""
import csv
import operator
import os
from datetime import datetime
from decimal import Decimal

_DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
_BOOL_TRUE = {"true", "1", "yes", "y", "是"}
_BOOL_FALSE = {"false", "0", "no", "n", "否"}
_COMPARE = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
_CONFLICT_POLICIES = ("error", "skip", "overwrite")
_FORMULA_POLICIES = ("error", "keep")


def load(path, schema):
    if isinstance(schema, dict):
        return _load_validated(path, schema)
    return _load_plain(path, schema)


def _load_plain(path, schema):
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            rows.append({name: raw.get(name, "") for name in schema})
    return rows


def _resolve_sheet(path, selector):
    if os.path.isdir(path):
        names = sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(path)
            if name.endswith(".csv")
        )
    else:
        names = [os.path.splitext(os.path.basename(path))[0]]
    if not names:
        raise ValueError("未找到工作表: %s" % path)
    if selector is None:
        index = 0
    elif isinstance(selector, int):
        index = selector
        if not 0 <= index < len(names):
            raise ValueError("工作表序号越界: %s（共 %d 张）" % (selector, len(names)))
    else:
        if selector not in names:
            raise ValueError("工作表不存在: %s（可选: %s）" % (selector, ", ".join(names)))
        index = names.index(selector)
    name = names[index]
    if os.path.isdir(path):
        return os.path.join(path, name + ".csv"), name
    return path, name


def _normalize_field(field):
    if isinstance(field, str):
        return {"name": field}
    return dict(field)


def _convert(field, value):
    kind = field.get("type", "str")
    if kind == "str":
        return value
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "decimal":
        return Decimal(value)
    if kind == "bool":
        lowered = value.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        raise ValueError("无法识别的布尔值")
    if kind in ("date", "datetime"):
        parsed = None
        for fmt in field.get("formats", _DEFAULT_DATE_FORMATS):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("无法识别的日期")
        return parsed.date() if kind == "date" else parsed
    raise ValueError("未知类型: %s" % kind)


def _parse_row(fields, position, cells, previous, formula_policy, lineno):
    record = {}
    row_errors = []
    for field in fields:
        name = field["name"]
        index = position.get(name)
        value = cells[index] if index is not None and index < len(cells) else ""
        if field.get("merged"):
            if value:
                previous[name] = value
            elif name in previous:
                value = previous[name]
        if value.startswith("="):
            if formula_policy == "error":
                row_errors.append({"row": lineno, "column": name, "message": "公式无计算值"})
                continue
            record[name] = value
            continue
        if value == "":
            if field.get("required"):
                row_errors.append({"row": lineno, "column": name, "message": "必填列为空"})
                continue
            record[name] = None
            continue
        try:
            converted = _convert(field, value)
        except (ValueError, ArithmeticError):
            row_errors.append({"row": lineno, "column": name, "message": "类型转换失败: %s" % value})
            continue
        enum = field.get("enum")
        if enum is not None and converted not in enum:
            row_errors.append({"row": lineno, "column": name, "message": "枚举外取值: %s" % value})
            continue
        if field.get("min") is not None and converted < field["min"]:
            row_errors.append({"row": lineno, "column": name, "message": "小于下限 %s" % field["min"]})
            continue
        if field.get("max") is not None and converted > field["max"]:
            row_errors.append({"row": lineno, "column": name, "message": "超过上限 %s" % field["max"]})
            continue
        record[name] = converted
    return record, row_errors


def _check_constraints(constraints, record, lineno):
    errors = []
    for constraint in constraints:
        left = record.get(constraint["left"])
        right = record.get(constraint["right"])
        if left is None or right is None:
            continue
        op = constraint.get("op", "<=")
        if not _COMPARE[op](left, right):
            message = constraint.get("message") or "跨列约束失败: %s %s %s" % (
                constraint["left"], op, constraint["right"])
            errors.append({"row": lineno, "column": constraint["left"], "message": message})
    return errors


def _load_validated(path, schema):
    fields = [_normalize_field(field) for field in schema["fields"]]
    sheet_path, sheet_name = _resolve_sheet(path, schema.get("sheet"))
    key_fields = schema.get("key") or []
    if isinstance(key_fields, str):
        key_fields = [key_fields]
    on_conflict = schema.get("on_conflict", "error")
    if on_conflict not in _CONFLICT_POLICIES:
        raise ValueError("未知冲突策略: %s" % on_conflict)
    formula_policy = schema.get("formula", "error")
    if formula_policy not in _FORMULA_POLICIES:
        raise ValueError("未知公式策略: %s" % formula_policy)
    dry_run = bool(schema.get("dry_run", False))
    batch_size = max(1, int(schema.get("batch_size", 500)))
    constraints = schema.get("constraints", [])

    rows = []
    errors = []
    conflicts = []
    skipped_empty = 0
    previous = {}
    seen = {}

    with open(sheet_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
        position = {name: index for index, name in enumerate(header)}
        for lineno, raw in enumerate(reader, start=2):
            cells = [cell.strip() for cell in raw]
            if not any(cells):
                skipped_empty += 1
                continue
            record, row_errors = _parse_row(fields, position, cells, previous, formula_policy, lineno)
            if not row_errors:
                row_errors.extend(_check_constraints(constraints, record, lineno))
            errors.extend(row_errors)
            if row_errors:
                continue
            if key_fields:
                key = tuple(record.get(name) for name in key_fields)
                if key in seen:
                    conflicts.append({"row": lineno, "key": key, "policy": on_conflict})
                    if on_conflict == "error":
                        errors.append({"row": lineno, "column": None,
                                       "message": "业务键重复: %s" % (key,)})
                        continue
                    if on_conflict == "skip":
                        continue
                    rows[seen[key]] = record
                    continue
                seen[key] = len(rows)
            rows.append(record)

    batches = []
    if not dry_run:
        batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]
        commit = schema.get("commit")
        if commit is not None:
            for batch in batches:
                commit(batch)

    return {
        "sheet": sheet_name,
        "dry_run": dry_run,
        "total": len(rows),
        "rows": rows,
        "batches": batches,
        "errors": errors,
        "conflicts": conflicts,
        "skipped_empty": skipped_empty,
    }
