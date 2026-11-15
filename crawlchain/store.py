"""链路事件存储：按任务一个 JSONL 文件，跨进程追加安全，支持保留期清理。"""

import fcntl
import json
import os
import time

DEFAULT_DIR = os.path.expanduser("~/.crawlchain")


def resolve_dir(path=None):
    d = path or os.environ.get("CRAWLCHAIN_DIR") or DEFAULT_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _file(task, d):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task)
    return os.path.join(d, safe + ".jsonl")


def append(task, event, d=None):
    """追加一条事件，加文件锁，多进程同时写不串行。"""
    path = _file(task, resolve_dir(d))
    line = json.dumps(event, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def load(task, d=None):
    path = _file(task, resolve_dir(d))
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass  # 跳过写了一半的坏行
    return events


def cleanup(task=None, retention_hours=24.0, data_dir=None, now=None):
    """按保留期清理链路数据（默认每任务保留最近一天），返回 {任务: 删除条数}。"""
    d = resolve_dir(data_dir)
    cutoff = (now if now is not None else time.time()) - retention_hours * 3600
    if task:
        tasks = [task]
    else:
        tasks = [fn[:-len(".jsonl")] for fn in os.listdir(d) if fn.endswith(".jsonl")]
    removed = {}
    for t in tasks:
        events = load(t, d)
        keep = [e for e in events if e.get("ts", 0) >= cutoff]
        if len(keep) != len(events):
            path = _file(t, d)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
            removed[t] = len(events) - len(keep)
    return removed
