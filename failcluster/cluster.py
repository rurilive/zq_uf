"""采集失败归因：按错误类型、站点、选择器和堆栈特征聚类。"""

import hashlib
import os
import re
import time

SAMPLE_LIMIT = 3
SPORADIC_MAX_COUNT = 2
REGRESSION_MIN_GAP = 3600.0

_state = {"failures": []}


def reset():
    _state["failures"] = []


def record(failure):
    """记一条失败，返回累计总数。缺省补 ts 为当前时间。"""
    entry = dict(failure)
    entry.setdefault("ts", time.time())
    _state["failures"].append(entry)
    return len(_state["failures"])


def report(since=None, until=None):
    """汇总报告，可按 [since, until] 时间窗过滤（epoch 秒）。

    返回 total/last；窗口内失败数 >= 2 时附带 clusters，
    按影响任务数、持续时间降序排列。
    """
    failures = [
        f for f in _state["failures"]
        if (since is None or f["ts"] >= since)
        and (until is None or f["ts"] <= until)
    ]
    result = {
        "total": len(failures),
        "last": failures[-1] if failures else None,
    }
    if len(failures) >= 2:
        result["clusters"] = _cluster(failures)
    return result


def _stack_signature(stack):
    """堆栈特征：取前 3 帧的文件名+函数名，忽略行号，取短哈希。"""
    if not stack:
        return None
    frames = re.findall(r'File "([^"]+)", line \d+, in (\w+)', stack)
    if frames:
        sig = "|".join(
            "%s:%s" % (os.path.basename(path), func)
            for path, func in frames[:3]
        )
    else:
        sig = re.sub(r"\d+", "", stack)
    return hashlib.md5(sig.encode("utf-8")).hexdigest()[:8]


def _cluster_key(failure):
    return (
        failure.get("error"),
        failure.get("site"),
        failure.get("selector"),
        _stack_signature(failure.get("stack")),
    )


def _task_of(failure, index):
    task = failure.get("task") or failure.get("url")
    return task if task is not None else "#%d" % index


def _category(times):
    """偶发：次数少；回归：间隔出现大空隙后复发；其余为持续。"""
    if len(times) <= SPORADIC_MAX_COUNT:
        return "sporadic"
    gaps = [b - a for a, b in zip(times, times[1:])]
    if gaps and max(gaps) >= REGRESSION_MIN_GAP:
        return "regression"
    return "persistent"


def _cluster(failures):
    groups = {}
    for index, failure in enumerate(failures):
        groups.setdefault(_cluster_key(failure), []).append((index, failure))
    clusters = []
    for (error, site, selector, stack_sig), members in groups.items():
        times = sorted(f["ts"] for _, f in members)
        tasks = sorted({_task_of(f, i) for i, f in members})
        samples = []
        for _, failure in members:
            if len(samples) < SAMPLE_LIMIT:
                samples.append(failure)
            else:
                samples[-1] = failure
        clusters.append({
            "key": {
                "error": error,
                "site": site,
                "selector": selector,
                "stack": stack_sig,
            },
            "count": len(members),
            "affected_tasks": len(tasks),
            "tasks": tasks,
            "first_seen": times[0],
            "last_seen": times[-1],
            "duration": times[-1] - times[0],
            "category": _category(times),
            "samples": samples,
        })
    clusters.sort(
        key=lambda c: (-c["affected_tasks"], -c["duration"], -c["count"])
    )
    return clusters
