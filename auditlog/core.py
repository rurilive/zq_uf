"""按批记录条数并回放计数。"""
import json


def record(batch, path):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(batch, ensure_ascii=False) + "\n")


def replay(path):
    fixed = 0
    failed = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            batch = json.loads(line)
            fixed += batch.get("ok", 0)
            failed += batch.get("failed", 0)
    return {"fixed": fixed, "failed": failed}
