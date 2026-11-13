"""从数据行推断字段名、类型和空值率。"""
import csv

_NULLS = {"", "null", "none", "na", "n/a"}


def _is_null(value):
    if value is None:
        return True
    return str(value).strip().lower() in _NULLS


def _is_int(text):
    try:
        int(text)
        return True
    except ValueError:
        return False


def _is_float(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def _infer_type(values):
    kinds = set()
    for value in values:
        text = str(value).strip()
        if _is_int(text):
            kinds.add("int")
        elif _is_float(text):
            kinds.add("float")
        elif text.lower() in ("true", "false"):
            kinds.add("bool")
        else:
            kinds.add("str")
    if not kinds:
        return "unknown"
    if kinds <= {"int"}:
        return "int"
    if kinds <= {"int", "float"}:
        return "float"
    if kinds == {"bool"}:
        return "bool"
    if len(kinds) == 1:
        return kinds.pop()
    return "str"


def infer_fields(rows):
    rows = list(rows)
    names = []
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    fields = []
    for name in names:
        values = [row.get(name) for row in rows]
        present = [value for value in values if not _is_null(value)]
        null_rate = round((len(values) - len(present)) / len(values), 4) if values else 0.0
        fields.append({"name": name, "type": _infer_type(present), "null_rate": null_rate})
    return fields


def infer_from_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return infer_fields(csv.DictReader(handle))
