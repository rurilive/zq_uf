"""CSV修复，当前按逗号切分并补齐列数。"""
import os


def repair(path, schema):
    """逐行切分补齐，引号和换行完全不处理。"""
    width = len(schema)
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split(",")
            if len(parts) < width:
                parts += [""] * (width - len(parts))
            rows.append(parts[:width])
    out = path + ".repaired"
    with open(out, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(",".join(row) + "\n")
    return {"rows": rows, "count": len(rows), "out": out}
