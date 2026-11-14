"""分区合并，当前先删后写。"""
import json
import os


def _files(partition):
    if not os.path.isdir(partition):
        return []
    return sorted(os.path.join(partition, n) for n in os.listdir(partition))


def compact(partition, rows):
    """先删旧文件再写新文件，失败留半份。"""
    for path in _files(partition):
        os.remove(path)
    os.makedirs(partition, exist_ok=True)
    target = os.path.join(partition, "merged.jsonl")
    with open(target, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return target


def query(partition):
    target = os.path.join(partition, "merged.jsonl")
    if not os.path.exists(target):
        return []
    rows = []
    with open(target, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows
