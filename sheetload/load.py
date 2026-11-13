"""业务表格导入：带校验的加载流程。

load(path, schema) 两种用法：
- schema 为列名列表：保持旧行为，返回 dict 列表（缺列补空串）；
- schema 为 dict：按声明解析、校验，返回 Report。
"""
import ast
import csv
import datetime
import operator
import os
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class RowError:
    line: int        # 文件物理行号（表头为第 1 行）
    column: str
    value: object
    reason: str


@dataclass
class Report:
    sheet: str
    rows: list = field(default_factory=list)       # 校验通过的数据行
    errors: list = field(default_factory=list)     # RowError 列表
    blank_rows: int = 0                            # 跳过的空行数
    conflicts: list = field(default_factory=list)  # 重复业务键冲突记录
    batches: list = field(default_factory=list)    # 正式导入的分批结果
    dry_run: bool = False

    @property
    def committed(self):
        return sum(len(batch) for batch in self.batches)


def load(path, schema):
    if isinstance(schema, dict):
        return _load_validated(path, schema)
    return _load_simple(path, schema)


def _load_simple(path, schema):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            rows.append({name: raw.get(name, "") for name in schema})
    return rows


def _to_date(value):
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("无法解析日期: %r" % text)


def _to_bool(value):
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "是"):
        return True
    if text in ("0", "false", "no", "n", "否"):
        return False
    raise ValueError("无法解析布尔值: %r" % text)


def _numeric(convert):
    def inner(value):
        if isinstance(value, (int, float, Decimal)):
            return convert(value)
        return convert(str(value).strip())
    return inner


_CONVERTERS = {
    "str": lambda value: value if isinstance(value, str) else str(value),
    "int": _numeric(int),
    "float": _numeric(float),
    "decimal": _numeric(Decimal),
    "date": _to_date,
    "bool": _to_bool,
}

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


def _safe_eval(expr, names):
    """只允许常量、列名、四则运算、比较和 and/or 的安全求值。"""
    def walk(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)):
                return node.value
            raise ValueError("不支持的常量")
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ValueError("未知名称: %s" % node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                func = _CMP_OPS.get(type(op))
                right = walk(comparator)
                if func is None or not func(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            values = [walk(item) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        raise ValueError("不支持的表达式")

    return walk(ast.parse(expr, mode="eval").body)


def _normalize_column(spec):
    if isinstance(spec, str):
        spec = {"name": spec}
    return {
        "name": spec["name"],
        "type": spec.get("type", "str"),
        "required": bool(spec.get("required", False)),
        "enum": spec.get("enum"),
        "range": spec.get("range"),
        "fill_down": bool(spec.get("fill_down", False)),
    }


def _select_sheet(path, selector):
    """path 为目录时每个 CSV 是一个工作表，按名字或序号选择。"""
    if os.path.isdir(path):
        files = sorted(name for name in os.listdir(path) if name.endswith(".csv"))
        names = [os.path.splitext(name)[0] for name in files]
        if not names:
            raise ValueError("目录下没有 CSV 工作表: %s" % path)
        if selector is None:
            index = 0
        elif isinstance(selector, int):
            if not 0 <= selector < len(names):
                raise ValueError("工作表序号越界: %r" % selector)
            index = selector
        else:
            if selector not in names:
                raise ValueError("找不到工作表: %r" % selector)
            index = names.index(selector)
        return os.path.join(path, files[index]), names[index]
    name = os.path.splitext(os.path.basename(path))[0]
    if selector is not None and selector not in (0, name):
        raise ValueError("找不到工作表: %r" % selector)
    return path, name


def _parse_row(row, line, columns, positions, previous, formulas, report):
    record = {}
    ok = True
    for column in columns:
        name = column["name"]
        index = positions.get(name)
        raw = row[index].strip() if index is not None and index < len(row) else ""
        if raw == "" and column["fill_down"] and name in previous:
            raw = previous[name]
        if column["fill_down"]:
            previous[name] = raw
        if raw == "":
            if column["required"]:
                report.errors.append(RowError(line, name, raw, "必填列缺失"))
                ok = False
            else:
                record[name] = None
            continue
        if raw.startswith("="):
            if formulas != "value":
                report.errors.append(RowError(line, name, raw, "公式未允许"))
                ok = False
                continue
            try:
                raw = _safe_eval(raw[1:], {})
            except Exception as exc:
                report.errors.append(RowError(line, name, raw, "公式求值失败: %s" % exc))
                ok = False
                continue
        try:
            value = _CONVERTERS[column["type"]](raw)
        except Exception:
            report.errors.append(RowError(line, name, raw, "类型转换失败(%s)" % column["type"]))
            ok = False
            continue
        if column["enum"] is not None and value not in column["enum"]:
            report.errors.append(RowError(line, name, value, "枚举值不合法"))
            ok = False
        elif column["range"] is not None:
            low, high = column["range"]
            if (low is not None and value < low) or (high is not None and value > high):
                report.errors.append(RowError(line, name, value, "超出范围"))
                ok = False
        record[name] = value
    return record, ok


def _check_constraints(record, line, constraints, report):
    ok = True
    for item in constraints:
        if isinstance(item, str):
            expr, message = item, item
        else:
            expr, message = item["expr"], item.get("message", item["expr"])
        try:
            passed = bool(_safe_eval(expr, record))
        except Exception as exc:
            passed, message = False, "%s (%s)" % (message, exc)
        if not passed:
            report.errors.append(RowError(line, "(跨列)", None, "约束失败: %s" % message))
            ok = False
    return ok


def _load_validated(path, schema):
    columns = [_normalize_column(item) for item in schema["columns"]]
    sheet_path, sheet_name = _select_sheet(path, schema.get("sheet"))
    formulas = schema.get("formulas", "error")
    on_conflict = schema.get("on_conflict", "skip")
    key = list(schema.get("key") or [])
    constraints = schema.get("constraints") or []
    batch_size = int(schema.get("batch_size", 500))
    dry_run = bool(schema.get("dry_run", False))

    report = Report(sheet=sheet_name, dry_run=dry_run)
    previous = {}
    seen = {}

    with open(sheet_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return report
        positions = {name: idx for idx, name in enumerate(header)}

        for row in reader:
            line = reader.line_num
            if not row or all(not cell.strip() for cell in row):
                report.blank_rows += 1
                continue
            record, ok = _parse_row(row, line, columns, positions, previous, formulas, report)
            if ok:
                ok = _check_constraints(record, line, constraints, report)
            if not ok:
                continue
            if key:
                business_key = tuple(record.get(name) for name in key)
                if business_key in seen:
                    report.conflicts.append(
                        {"key": business_key, "line": line, "action": on_conflict})
                    if on_conflict == "overwrite":
                        report.rows[seen[business_key]] = record
                    elif on_conflict == "error":
                        report.errors.append(
                            RowError(line, ",".join(key), business_key, "业务键重复"))
                    continue
                seen[business_key] = len(report.rows)
            report.rows.append(record)

    if not dry_run:
        report.batches = [report.rows[i:i + batch_size]
                          for i in range(0, len(report.rows), batch_size)]
    return report
