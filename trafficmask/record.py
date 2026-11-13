"""采集流量录制，当前原样落盘。"""
import json
import os

DEFAULT_PATH = "traffic.jsonl"


def record(request, path=None):
    """把请求完整写进文件，敏感字段不处理。"""
    target = path or DEFAULT_PATH
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(request, ensure_ascii=False) + "\n")
    return os.path.abspath(target)


def replay(path):
    """按行读回并原样返回。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
