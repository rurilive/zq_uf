"""按schema版本兼容读取JSONL数据。"""
import json


class SchemaMismatch(ValueError):
    """文件字段和schema不完全一致。"""


class IncompatibleSchema(SchemaMismatch):
    """版本差异无法兼容，整文件拒绝并给出原因。"""


class CompatReport:
    """一次版本兼容读取的差异报告，所有问题显式记录，不静默跳过。"""

    def __init__(self):
        self.dropped_columns = []    # 文件里有、schema已删除的列名
        self.filled_defaults = {}    # 缺列补齐的默认值 {列名: 默认值}
        self.renames_applied = {}    # 按映射表还原的改名 {旧名: 新名}
        self.conversions = []        # 成功的类型转换 (行号, 字段, 原类型, 目标类型)
        self.conversion_errors = []  # 失败的类型转换 (行号, 字段, 原值, 目标类型)

    def __repr__(self):
        return (
            "CompatReport(dropped=%s, defaults=%s, renames=%s, "
            "conversions=%d, conversion_errors=%d)"
            % (
                self.dropped_columns,
                self.filled_defaults,
                self.renames_applied,
                len(self.conversions),
                len(self.conversion_errors),
            )
        )


class ReadResult(list):
    """读取结果，行为等同list，附带report属性。"""

    def __init__(self, rows, report=None):
        super().__init__(rows)
        self.report = report


def read(path, schema):
    """读取JSONL。schema为list时严格匹配；为dict时按版本兼容读取。

    版本化schema格式::

        {
            "fields": {"id": int, "name": str, "amount": float},
            "defaults": {"amount": 0},   # 缺列时补的默认值，可选
            "renames": {"title": "name"} # 文件旧列名 -> 现字段名，可选
        }
    """
    if isinstance(schema, dict):
        return _read_compat(path, _validate_spec(schema))
    return ReadResult(_read_strict(path, schema))


def _read_strict(path, schema):
    """要求每行列名和schema完全一致，不做补齐和类型转换。"""
    expected = list(schema)
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            missing = [name for name in expected if name not in raw]
            extra = [name for name in raw if name not in expected]
            if missing or extra:
                raise SchemaMismatch(
                    "line %d: missing=%s extra=%s" % (line_no, missing, extra)
                )
            rows.append({name: raw[name] for name in expected})
    return rows


def _validate_spec(spec):
    fields = spec.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("schema缺少非空的'fields'字典: %r" % (spec,))
    for name, ftype in fields.items():
        if not isinstance(ftype, type):
            raise ValueError("字段%r的类型必须是type，得到%r" % (name, ftype))
    defaults = spec.get("defaults", {})
    renames = spec.get("renames", {})
    for name in defaults:
        if name not in fields:
            raise ValueError("默认值指向未知字段%r" % (name,))
    for old, new in renames.items():
        if new not in fields:
            raise ValueError("改名映射%r -> %r的目标不在fields中" % (old, new))
    return fields, defaults, renames


def _read_compat(path, spec):
    fields, defaults, renames = spec
    report = CompatReport()
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise IncompatibleSchema(
                    "line %d: 行不是JSON对象，无法按schema兼容: %r" % (line_no, raw)
                )
            rows.append(_convert_row(raw, line_no, fields, defaults, renames, report))
    return ReadResult(rows, report)


def _convert_row(raw, line_no, fields, defaults, renames, report):
    row = dict(raw)
    for old, new in renames.items():
        if old not in row:
            continue
        if new in row:
            raise IncompatibleSchema(
                "line %d: 旧列%r和新列%r同时出现，改名映射冲突" % (line_no, old, new)
            )
        row[new] = row.pop(old)
        report.renames_applied[old] = new
    for name in fields:
        if name not in row:
            if name not in defaults:
                raise IncompatibleSchema(
                    "line %d: 缺少字段%r且schema未提供默认值，整文件拒绝"
                    % (line_no, name)
                )
            row[name] = defaults[name]
            report.filled_defaults[name] = defaults[name]
    for name in list(row):
        if name not in fields:
            if name not in report.dropped_columns:
                report.dropped_columns.append(name)
            del row[name]
    for name, ftype in fields.items():
        value = row[name]
        if isinstance(value, ftype):
            continue
        try:
            converted = ftype(value)
        except (TypeError, ValueError):
            report.conversion_errors.append((line_no, name, value, ftype.__name__))
            continue
        report.conversions.append(
            (line_no, name, type(value).__name__, ftype.__name__)
        )
        row[name] = converted
    return row
