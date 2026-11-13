"""单文件追加写入。"""
import json


def write(records, path):
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def read(path, date=None):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if date and record.get("date") != date:
                continue
            rows.append(record)
    return rows
