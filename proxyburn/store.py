"""跨进程共享状态：JSON 状态文件 + JSONL 审计日志，flock 保护。

多进程/多机把 PROXYBURN_STATE_DIR 指向同一共享目录（如 NFS），
状态与审计即保持一致。
"""

import contextlib
import fcntl
import json
import os

STATE_DIR = os.environ.get("PROXYBURN_STATE_DIR", "/tmp/proxyburn")


def _paths():
    return (
        os.path.join(STATE_DIR, "state.json"),
        os.path.join(STATE_DIR, "audit.jsonl"),
    )


@contextlib.contextmanager
def locked():
    """排他锁内读出状态，正常退出时原子写回；审计追加也受同一把锁保护。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    state_path, _ = _paths()
    with open(os.path.join(STATE_DIR, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                data = json.load(f)
        yield data
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, state_path)


def append_audit(entry):
    """追加一条审计（须在 locked() 内调用）。"""
    _, audit_path = _paths()
    with open(audit_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit():
    _, audit_path = _paths()
    if not os.path.exists(audit_path):
        return []
    with open(audit_path) as f:
        return [json.loads(line) for line in f if line.strip()]
