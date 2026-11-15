"""采集链路追踪：一个标识贯穿入队到入库，重试同号，跨进程传号。"""

import os
import socket
import time
import uuid
from contextlib import contextmanager

from . import store
from .config import retention_hours


def new_trace_id():
    return "t-" + uuid.uuid4().hex[:12]


class Trace(dict):
    """一次采集的链路标识，dict 形态方便直接打印和序列化。"""

    def __init__(self, task, trace_id, attempt, d=None):
        super().__init__(trace=trace_id, task=task, attempt=attempt)
        self._d = d

    @contextmanager
    def stage(self, name):
        """记录一个阶段的开始、结束和结果；异常会记录后原样抛出。"""
        rec = stage_begin(self, name)
        error = None
        try:
            yield rec
        except Exception as exc:
            error = exc
            raise
        finally:
            stage_end(self, rec, error=error)

    def finish(self, ok=True, error=None):
        return finish(self, ok=ok, error=error)


def start(task, trace_id=None, attempt=None, data_dir=None):
    """开始或继续一条链路。

    - 新采集：start(task)，生成新标识，attempt=1。
    - 重试：start(task, trace_id=旧标识)，沿用同一标识，attempt 自动 +1。
    - 跨进程：start(task, trace_id=标识, attempt=次数)，子进程接着同一次尝试记。
    """
    store.cleanup(task=task, retention_hours=retention_hours(), data_dir=data_dir)
    tid = trace_id or new_trace_id()
    if attempt is None:
        existing = [e for e in store.load(task, data_dir) if e.get("trace") == tid]
        attempt = max((e.get("attempt", 0) for e in existing), default=0) + 1
    tr = Trace(task, tid, attempt, d=data_dir)
    store.append(task, {
        "type": "begin", "trace": tid, "task": task, "attempt": attempt,
        "ts": time.time(), "pid": os.getpid(), "host": socket.gethostname(),
    }, data_dir)
    return tr


def stage_begin(trace, name):
    rec = {
        "type": "stage_start", "trace": trace["trace"], "task": trace["task"],
        "attempt": trace["attempt"], "stage": name,
        "ts": time.time(), "pid": os.getpid(),
    }
    store.append(trace["task"], rec, trace._d)
    return rec


def stage_end(trace, rec, error=None):
    now = time.time()
    ok = error is None
    ev = {
        "type": "stage_end", "trace": rec["trace"], "task": rec["task"],
        "attempt": rec["attempt"], "stage": rec["stage"], "ts": now,
        "elapsed_ms": round((now - rec["ts"]) * 1000, 3),
        "ok": ok, "error": None,
    }
    if not ok:
        ev["error"] = f"{type(error).__name__}: {error}"
    store.append(rec["task"], ev, trace._d)
    return ev


def finish(trace, ok=True, error=None):
    ev = {
        "type": "end", "trace": trace["trace"], "task": trace["task"],
        "attempt": trace["attempt"], "ts": time.time(), "ok": ok, "error": error,
    }
    store.append(trace["task"], ev, trace._d)
    return ev
