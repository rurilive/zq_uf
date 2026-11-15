"""采集任务的可恢复挂起。

hold(task)     分阶段挂起：stopping -> draining -> checkpointing -> releasing -> exited。
               先停止取新任务，等在跑请求收尾（带超时，超时强制收尾并标记半写文件），
               落检查点后释放代理、配额和文件句柄。
release(task)  先校验检查点，清理半写文件，再从检查点恢复进度继续。
重复 hold/release 幂等：同一轮内重复调用返回首次结果，不产生额外副作用。
纯标准库实现。
"""

import json
import os
import time

CHECKPOINT_VERSION = 1
DEFAULT_TIMEOUT = 5.0
POLL_INTERVAL = 0.01

# name -> {"held": report|None, "resumed": report|None}，保证幂等
_STATE = {}


class Request:
    """一个在跑请求；stuck=True 模拟收尾卡死，只能等超时强制收尾。"""

    def __init__(self, url, output=None, stuck=False):
        self.url = url
        self.output = output
        self.stuck = stuck
        self.finished = False
        self.aborted = False

    def finish(self):
        if not self.stuck:
            self.finished = True

    def abort(self):
        self.aborted = True
        self.finished = True


class Task:
    """采集任务运行态：在跑请求、代理、配额、文件句柄、进度与待抓队列。"""

    def __init__(self, name, workdir="."):
        self.name = name
        self.workdir = workdir
        self.accepting = True
        self.in_flight = []
        self.proxies = []
        self.quota = 0
        self.handles = []
        self.half_written = []
        self.progress = 0
        self.pending = []

    def checkpoint_path(self):
        return os.path.join(self.workdir, self.name + ".checkpoint.json")


def hold(task, timeout=DEFAULT_TIMEOUT):
    """挂起任务：等在跑请求收尾并落检查点，释放全部资源。重复调用幂等。"""
    name = task.name if isinstance(task, Task) else str(task)
    st = _STATE.setdefault(name, {"held": None, "resumed": None})
    if st["held"] is not None:
        return st["held"]
    st["resumed"] = None  # 新一轮挂起，作废上次恢复结果
    report = _do_hold(task if isinstance(task, Task) else None, name, timeout)
    st["held"] = report
    return report


def release(task):
    """恢复任务：校验检查点、清理半写文件、从检查点继续。重复调用幂等。"""
    name = task.name if isinstance(task, Task) else str(task)
    st = _STATE.setdefault(name, {"held": None, "resumed": None})
    if st["resumed"] is not None:
        return st["resumed"]
    st["held"] = None  # 恢复后允许下一轮挂起
    report = _do_release(task if isinstance(task, Task) else None, name)
    st["resumed"] = report
    return report


def _do_hold(task, name, timeout):
    phases = ["stopping"]
    released = []
    partial = []
    timed_out = False
    checkpoint = None
    if task is not None:
        task.accepting = False  # 停止取新任务
        phases.append("draining")
        timed_out, partial = _drain(task, timeout)
        for path in partial:
            if path not in task.half_written:
                task.half_written.append(path)
        phases.append("checkpointing")
        checkpoint = _write_checkpoint(task)
        phases.append("releasing")
        released = _release_resources(task)
    phases.append("exited")
    return {
        "task": name,
        "phase": "exited",
        "phases": phases,
        "released": released,
        "checkpoint": checkpoint,
        "partial_files": partial,
        "timed_out": timed_out,
    }


def _drain(task, timeout):
    """等在跑请求收尾；超时后强制收尾，半写文件标记出来，URL 重新入队。"""
    deadline = time.monotonic() + timeout
    for req in task.in_flight:
        req.finish()
    while not all(req.finished for req in task.in_flight):
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL)
    timed_out = False
    partial = []
    for req in task.in_flight:
        if not req.finished:
            req.abort()
            timed_out = True
            task.pending.append(req.url)  # 没跑完的重新入队，恢复后继续
            if req.output:
                partial.append(req.output)
    task.in_flight = []
    return timed_out, partial


def _write_checkpoint(task):
    path = task.checkpoint_path()
    data = {
        "version": CHECKPOINT_VERSION,
        "task": task.name,
        "progress": task.progress,
        "pending": list(task.pending),
        "partial_files": list(task.half_written),
        "held_at": time.time(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子落盘，避免检查点本身写一半
    return path


def _release_resources(task):
    released = []
    for proxy in task.proxies:
        released.append("proxy:%s" % proxy)
    task.proxies = []
    if task.quota:
        released.append("quota:%d" % task.quota)
        task.quota = 0
    for handle in task.handles:
        try:
            handle.close()
        except Exception:
            pass
        released.append("handle:%s" % getattr(handle, "name", "?"))
    task.handles = []
    return released


def _do_release(task, name):
    if task is None or not os.path.exists(task.checkpoint_path()):
        return {"task": name, "phase": "idle", "resumed": False}
    path = task.checkpoint_path()
    try:
        data = _validate_checkpoint(path, name)
    except ValueError as exc:
        return {"task": name, "phase": "idle", "resumed": False, "error": str(exc)}
    cleaned = []
    for partial in data["partial_files"]:
        if os.path.exists(partial):
            os.remove(partial)
            cleaned.append(partial)
    task.half_written = [p for p in task.half_written if p not in data["partial_files"]]
    task.progress = data["progress"]
    task.pending = list(data["pending"])
    task.accepting = True
    os.remove(path)  # 检查点已消费
    return {
        "task": name,
        "phase": "resumed",
        "resumed": True,
        "progress": task.progress,
        "pending": list(task.pending),
        "cleaned": cleaned,
    }


def _validate_checkpoint(path, name):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("检查点不可读: %s" % exc)
    if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
        raise ValueError("检查点版本不符")
    if data.get("task") != name:
        raise ValueError("检查点属于其他任务: %r" % data.get("task"))
    for key in ("progress", "pending", "partial_files"):
        if key not in data:
            raise ValueError("检查点缺少字段: %s" % key)
    return data
