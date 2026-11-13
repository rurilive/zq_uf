"""失败样本记录与回放的最小实现。"""
import json


def record(failure, path):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(failure, ensure_ascii=False) + "\n")


def replay(paths, fetch):
    counts = {"fixed": 0, "still_failed": 0}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                failure = json.loads(line)
                if fetch(failure.get("url", "")):
                    counts["fixed"] += 1
                else:
                    counts["still_failed"] += 1
    return counts
