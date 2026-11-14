"""按站点共享滑动窗口的失败重试调度。

同一站点的任务共享一个滑动时间窗和重试预算，窗口内加随机抖动，
避免多个任务同时打到刚恢复的站点；单任务超过总时长上限即放弃并记录原因。
"""

import random
import time

WINDOW_SECONDS = 30.0  # 站点共享滑动窗口长度（秒）
SITE_BUDGET = 8        # 每个窗口内同一站点允许的重试次数
BASE_DELAY = 1.0       # 首次重试的基础退避（秒），随尝试次数指数翻倍
MAX_ATTEMPTS = 3       # 单任务最大尝试次数，达到即放弃
MAX_DURATION = 120.0   # 单任务从首次失败算起的总时长上限（秒）
JITTER_MAX = 2.0       # 窗口内随机抖动上限（秒）

_clock = time.time     # 时钟，测试中可替换
_rng = random.Random()  # 抖动源，测试中可 seed

_tasks = {}      # task -> {"site", "attempts", "first_failure", "due_at"}
_sites = {}      # site -> {"window_start", "used"}
_abandoned = []  # {"task", "site", "reason", "attempts"}
_cancelled = set()


class RetryWindowError(Exception):
    """重试调度的非法操作：重复排期、取消未知任务、改挂站点等。"""


def _abandon(task, reason):
    item = _tasks.pop(task)
    _abandoned.append({
        "task": task,
        "site": item["site"],
        "reason": reason,
        "attempts": item["attempts"],
    })
    return {"task": task, "attempts": item["attempts"], "retry": False,
            "abandoned": True, "reason": reason}


def schedule(task, site):
    """记录一次失败并给出下次重试时间。

    同一任务只排期一次，重复失败只更新原条目；返回
    {"task", "attempts", "retry", "due_at"}，放弃时带 "abandoned"/"reason"。
    """
    if not isinstance(task, str) or not task:
        raise ValueError(f"task 必须是非空字符串，收到: {task!r}")
    if not isinstance(site, str) or not site:
        raise ValueError(f"site 必须是非空字符串，收到: {site!r}")
    if task in _cancelled:
        raise RetryWindowError(f"任务 {task!r} 已取消，不再排期")
    if any(a["task"] == task for a in _abandoned):
        raise RetryWindowError(f"任务 {task!r} 已放弃，不再排期")

    now = _clock()
    item = _tasks.get(task)
    if item is None:
        item = {"site": site, "attempts": 0, "first_failure": now, "due_at": None}
        _tasks[task] = item
    elif item["site"] != site:
        raise RetryWindowError(
            f"任务 {task!r} 已挂在站点 {item['site']!r}，不能改挂 {site!r}")

    item["attempts"] += 1
    if item["attempts"] >= MAX_ATTEMPTS:
        return _abandon(task, f"达到最大尝试次数 {MAX_ATTEMPTS}")
    if now - item["first_failure"] >= MAX_DURATION:
        return _abandon(task, f"超过总时长上限 {MAX_DURATION:.0f}s")

    # 站点共享滑动窗口：过期则整体滑动，预算归零
    win = _sites.setdefault(site, {"window_start": now, "used": 0})
    if now - win["window_start"] >= WINDOW_SECONDS:
        win["window_start"] = now
        win["used"] = 0

    delay = min(BASE_DELAY * 2 ** (item["attempts"] - 1), WINDOW_SECONDS)
    if win["used"] >= SITE_BUDGET:
        # 本窗口预算耗尽，顺延到下一个窗口起点，不消耗当前预算
        due_at = win["window_start"] + WINDOW_SECONDS
    else:
        win["used"] += 1
        due_at = now + delay
    due_at += _rng.uniform(0.0, JITTER_MAX)  # 窗口内抖动，错开同时重试

    if due_at - item["first_failure"] > MAX_DURATION:
        return _abandon(task, f"超过总时长上限 {MAX_DURATION:.0f}s")

    item["due_at"] = due_at
    return {"task": task, "attempts": item["attempts"],
            "retry": True, "due_at": due_at}


def due(now=None):
    """待重试任务；now 为 None 返回全部待重试，否则只返回到期任务（按到期时间排序）。"""
    if now is None:
        return list(_tasks)
    return [task for task, item in
            sorted(_tasks.items(), key=lambda kv: kv[1]["due_at"])
            if item["due_at"] <= now]


def cancel(task):
    """取消任务：从待重试队列移除，之后不再触发、不再排期。"""
    item = _tasks.pop(task, None)
    if item is None:
        raise RetryWindowError(f"无法取消：任务 {task!r} 不在待重试队列中")
    _cancelled.add(task)
    return {"task": task, "cancelled": True}


def windows(now=None):
    """各站点共享窗口状态：窗口区间、已用预算、剩余预算。"""
    now = _clock() if now is None else now
    out = {}
    for site, win in _sites.items():
        start, used = win["window_start"], win["used"]
        if now - start >= WINDOW_SECONDS:  # 窗口已滑过，按新窗口口径展示
            start, used = now, 0
        out[site] = {
            "window_start": start,
            "window_end": start + WINDOW_SECONDS,
            "used": used,
            "budget": SITE_BUDGET,
            "remaining": max(0, SITE_BUDGET - used),
        }
    return out


def abandoned():
    """已放弃任务列表，每条含 task/site/reason/attempts。"""
    return [dict(a) for a in _abandoned]
