"""分区查询，当前全量扫描再过滤。"""
import json
import os

ROOT = "warehouse"


def query(dataset, filters):
    """所有分区文件都读一遍，在内存里过滤。"""
    base = os.path.join(ROOT, dataset)
    if not os.path.isdir(base):
        return {"rows": [], "scanned": 0, "missing": False}
    rows = []
    scanned = 0
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        scanned += 1
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if all(row.get(key) == value for key, value in filters.items()):
                    rows.append(row)
    return {"rows": rows, "scanned": scanned, "missing": False}
