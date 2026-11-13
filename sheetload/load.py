"""业务表格导入的最小实现。"""
import csv


def load(path, schema):
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            rows.append({name: raw.get(name, "") for name in schema})
    return rows
