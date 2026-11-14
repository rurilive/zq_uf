"""采集日志，当前直接追加JSON行。"""
import json


def append(record, path):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def recover(path):
    """遇到半行直接抛错，不跳过。"""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows
