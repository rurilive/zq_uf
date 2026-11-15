"""任务窗口：时间窗口 + 上游依赖 + 补跑 + 并发资源组。

对外接口保持 ``open(window)`` / ``close(window)``。``window`` 是一个字典：

    name                窗口名（必填）
    upstream            上游任务，可写字符串、字典或列表；字典可带 done/status/check
    dependency_policy   上游未完成时 "wait"（默认）或 "skip"
    wait_timeout        等待上游或并发槽位的上限秒数（默认 2 秒）
    scheduled_at        窗口计划开始时间，也接受 window_start/start
    window_seconds      窗口时长，也接受 duration
    window_end          窗口结束时间，也接受 deadline
    catch_up_policy     错过窗口时 "once"（默认，只补一次）或 "never"
    max_concurrency     资源组内并发上限，也接受 concurrency_limit
    resource_group      资源组，也接受 group（默认 "default"）
    tasks               窗口内任务，字符串或字典；未完成的任务在 close 时清理
    now                 仅测试用：覆盖当前时间，用于判定窗口是否错过

纯标准库实现。时钟与 sleep 可通过 ``configure(clock=..., sleep=...)`` 注入，
便于测试不依赖真实等待时间。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

_DEFAULT_WAIT_TIMEOUT = 2.0
_POLL_INTERVAL = 0.05

_COMPLETED_STATUSES = {
    "completed",
    "complete",
    "done",
    "success",
    "succeeded",
    "finished",
    "ok",
    "true",
}
_FAILED_STATUSES = {"failed", "failure", "error", "errored", "aborted", "fatal"}

# 运行期状态：任务登记表、已开启窗口、资源组、已补跑过的窗口
# （模块级，便于像原实现一样直接调用 open/close）
_clock = time.time
_sleep = time.sleep
# 可重入：open/close/status 内部可能互相调用
_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_windows: dict[str, dict] = {}
_groups: dict[str, set] = {}
_group_limits: dict[str, int] = {}
_catch_up_done: set = set()


# ---------------------------------------------------------------------------
# 运行环境
# ---------------------------------------------------------------------------
def configure(*, clock=None, sleep=None):
    """注入时钟与 sleep，测试可用假时钟；传 None 表示保持当前设置。"""
    global _clock, _sleep
    if clock is not None:
        _clock = clock
    if sleep is not None:
        _sleep = sleep


def reset():
    """清空任务窗口运行期状态（测试与 demo 使用），不重置时钟。"""
    with _lock:
        _jobs.clear()
        _windows.clear()
        _groups.clear()
        _group_limits.clear()
        _catch_up_done.clear()


# ---------------------------------------------------------------------------
# 上游任务登记
# ---------------------------------------------------------------------------
def mark_job(name, status="completed", detail=None):
    """登记上游任务状态：completed / pending / failed。"""
    normalized = _normalize_status(status)
    with _lock:
        _jobs[str(name)] = {"status": normalized, "detail": detail}
    return {"name": str(name), "status": normalized, "detail": detail}


def fail_job(name, detail=None):
    """登记上游失败；在该上游完成/重置前，下游窗口不会启动。"""
    return mark_job(name, "failed", detail)


# 兼容更直观的别名
set_job_status = mark_job
register_job = mark_job


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _normalize_status(value):
    if isinstance(value, bool):
        return "completed" if value else "pending"
    text = str(value).strip().lower()
    if text in _COMPLETED_STATUSES:
        return "completed"
    if text in _FAILED_STATUSES:
        return "failed"
    return "pending"


def _first_present(mapping, keys, default=None):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _name(window):
    if isinstance(window, str):
        return window
    return str(window.get("name", ""))


def _group(window):
    raw = _first_present(window, ("resource_group", "group", "resource"), "default")
    if raw is None or raw == "":
        return "default"
    return str(raw)


def _limit(window):
    raw = _first_present(
        window,
        ("max_concurrency", "concurrency_limit", "slot_limit", "max_parallel"),
        None,
    )
    try:
        window_limit = None if raw is None else max(0, int(raw))
    except (TypeError, ValueError):
        window_limit = None
    group_limit = _group_limits.get(_group(window))
    if window_limit is None:
        return group_limit
    if group_limit is None:
        return window_limit
    return min(window_limit, group_limit)


def set_group_limit(group, limit):
    """设置资源组的全局并发上限；窗口级 max_concurrency 取更严格者。"""
    with _lock:
        if limit is None:
            _group_limits.pop(str(group), None)
        else:
            _group_limits[str(group)] = max(0, int(limit))
    return {"group": str(group), "limit": None if limit is None else int(limit)}


def _wait_timeout(window):
    raw = _first_present(
        window,
        ("wait_timeout", "max_wait", "max_wait_seconds", "timeout"),
        _DEFAULT_WAIT_TIMEOUT,
    )
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_WAIT_TIMEOUT


def _timestamp(value):
    """把数字/ISO 字符串/datetime 统一成秒。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _dependency_policy(window):
    raw = _first_present(
        window,
        ("dependency_policy", "on_missing_upstream", "on_pending", "upstream_policy"),
        None,
    )
    if raw is None:
        for key in ("wait_for_upstream", "wait_upstream"):
            if key in window:
                raw = "wait" if window[key] else "skip"
                break
    if raw is None:
        return "wait"
    return "wait" if str(raw).strip().lower() in {"wait", "block"} else "skip"


def _catch_up_policy(window):
    raw = _first_present(window, ("catch_up_policy", "on_missed_window"), None)
    if raw is None and "catch_up" in window:
        value = window["catch_up"]
        if isinstance(value, bool):
            raw = "once" if value else "never"
        else:
            raw = value
    if raw is None:
        return "once"
    text = str(raw).strip().lower()
    return "never" if text in {"never", "none", "no", "false", "skip"} else "once"


def _concurrency_policy(window):
    raw = _first_present(
        window,
        ("on_concurrency_limit", "concurrency_policy", "slot_policy"),
        None,
    )
    if raw is None:
        return "skip"
    return "wait" if str(raw).strip().lower() == "wait" else "skip"


# ---------------------------------------------------------------------------
# 上游依赖解析与校验
# ---------------------------------------------------------------------------
def _upstream_specs(window):
    raw = _first_present(
        window,
        ("upstream", "upstreams", "depends_on", "dependencies"),
        [],
    )
    if raw is None:
        return []
    if isinstance(raw, str):
        return [{"name": raw}]
    if isinstance(raw, dict):
        spec_keys = {
            "name",
            "job",
            "task",
            "check",
            "done",
            "completed",
            "failed",
            "status",
            "state",
            "detail",
        }
        if spec_keys.intersection(raw):
            return [dict(raw)]
        specs = []
        for name, value in raw.items():
            spec = {"name": str(name)}
            if isinstance(value, dict):
                spec.update(value)
            elif isinstance(value, bool):
                spec["done"] = value
            elif value is not None:
                spec["status"] = value
            specs.append(spec)
        return specs
    if isinstance(raw, (list, tuple, set)):
        specs = []
        for item in raw:
            if isinstance(item, str):
                specs.append({"name": item})
            elif isinstance(item, dict):
                specs.append(dict(item))
        return specs
    return []


def _spec_name(spec):
    return str(spec.get("name") or spec.get("job") or spec.get("task") or "")


def _status_of(spec):
    """返回 (completed/pending/failed, detail)。"""
    name = _spec_name(spec)
    check = spec.get("check")
    if callable(check):
        try:
            value = check()
        except Exception as exc:  # 上游检查函数异常按失败处理
            return "failed", "check error: %s" % exc
        detail = None
        if isinstance(value, dict):
            detail = value.get("detail")
            status = value.get("status", value.get("done", value.get("completed")))
        else:
            status = value
        if status is None:
            status = "pending"
        return _normalize_status(status), detail

    for key in ("failed", "error"):
        if spec.get(key):
            return "failed", str(spec[key]) if spec[key] is not True else spec.get("detail")
    if "done" in spec:
        return ("completed" if spec["done"] else "pending"), spec.get("detail")
    if "completed" in spec:
        return ("completed" if spec["completed"] else "pending"), spec.get("detail")
    status = spec.get("status", spec.get("state"))
    if status is not None:
        return _normalize_status(status), spec.get("detail")

    entry = _jobs.get(name)
    if entry is not None:
        return entry["status"], entry.get("detail")
    # 未登记的上游沿袭旧接口行为：视为已完成，保证既有调用不被阻塞。
    return "completed", "未登记的上游按已完成处理"


def _evaluate_upstreams(specs):
    failed = []
    pending = []
    for spec in specs:
        status, detail = _status_of(spec)
        if status == "failed":
            failed.append({"name": _spec_name(spec), "detail": detail})
        elif status != "completed":
            pending.append(_spec_name(spec))
    return failed, pending


def _validate_upstreams(window):
    specs = _upstream_specs(window)
    if not specs:
        return {
            "status": "ok",
            "waited": False,
            "wait_seconds": 0.0,
            "pending": [],
            "failed": [],
            "alerts": [],
            "reason": None,
        }

    policy = _dependency_policy(window)
    timeout = _wait_timeout(window)
    started = _clock()
    waited = False

    while True:
        failed, pending = _evaluate_upstreams(specs)
        elapsed = _clock() - started
        if failed:
            alerts = [
                "上游 %s 失败：%s"
                % (item["name"], item["detail"] or "未知原因")
                for item in failed
            ]
            return {
                "status": "failed",
                "waited": waited,
                "wait_seconds": elapsed,
                "pending": pending,
                "failed": failed,
                "alerts": alerts,
                "reason": "upstream_failed",
            }
        if not pending:
            return {
                "status": "ok",
                "waited": waited,
                "wait_seconds": elapsed,
                "pending": [],
                "failed": [],
                "alerts": [],
                "reason": None,
            }
        if policy == "skip":
            return {
                "status": "pending",
                "waited": waited,
                "wait_seconds": elapsed,
                "pending": pending,
                "failed": [],
                "alerts": [],
                "reason": "upstream_pending",
            }
        if elapsed >= timeout:
            alerts = [
                "等待上游 %s 超时（%ss）" % ("、".join(pending), timeout)
            ]
            return {
                "status": "pending",
                "waited": waited,
                "wait_seconds": elapsed,
                "pending": pending,
                "failed": [],
                "alerts": alerts,
                "reason": "upstream_timeout",
            }
        waited = True
        _sleep(min(_POLL_INTERVAL, timeout - elapsed))


# ---------------------------------------------------------------------------
# 窗口时间判定与补跑
# ---------------------------------------------------------------------------
def _fmt_ts(value):
    """把秒时间戳格式化成 ISO 文本，便于日志阅读。"""
    try:
        return datetime.fromtimestamp(value).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return str(value)


def _missed(window):
    if window.get("missed") is True:
        return True, window.get("missed_reason") or "标记为错过窗口"

    end = _timestamp(_first_present(window, ("window_end", "deadline", "end", "window_close"), None))
    start = _timestamp(
        _first_present(window, ("scheduled_at", "window_start", "start", "schedule"), None)
    )
    duration = _first_present(window, ("window_seconds", "duration", "length"), None)
    now = _timestamp(window.get("now"))
    if now is None:
        now = _clock()

    if end is not None and now > end:
        return True, "窗口已于 %s 结束，当前 %s" % (_fmt_ts(end), _fmt_ts(now))
    if start is not None and duration is not None:
        try:
            finish = start + float(duration)
        except (TypeError, ValueError):
            finish = None
        if finish is not None and now > finish:
            return True, "窗口 %s ~ %s 已结束，当前 %s" % (
                _fmt_ts(start),
                _fmt_ts(finish),
                _fmt_ts(now),
            )
    return False, None


def _catch_up_key(name, window):
    """同一窗口的同一场次只补跑一次；不同场次（时间不同）可各自补一次。"""
    start = _timestamp(
        _first_present(window, ("scheduled_at", "window_start", "start", "schedule"), None)
    )
    end = _timestamp(
        _first_present(window, ("window_end", "deadline", "end", "window_close"), None)
    )
    return (name, start, end)


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def _task_state(window):
    raw = window.get("tasks", [])
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, (list, tuple, set)):
        items = [(item, None) for item in raw]
    else:
        items = []
    tasks = {}
    for item, value in items:
        detail = {}
        if isinstance(item, str):
            name = item
            if isinstance(value, dict):
                detail = dict(value)
            elif isinstance(value, bool):
                detail["done"] = value
            elif value is not None:
                detail["status"] = value
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("task") or "")
            detail = dict(item)
        else:
            continue
        if not name:
            continue
        status = detail.get("status")
        if status is not None:
            done = _normalize_status(status) == "completed"
        else:
            done = bool(detail.get("done", detail.get("completed", False)))
        tasks[name] = {"done": done, "status": "completed" if done else "pending"}
    return tasks


def complete_task(window, task, status="completed"):
    """把窗口内某个任务标记为完成，关闭时不会进入 aborted。"""
    name = _name(window)
    task_name = task.get("name") if isinstance(task, dict) else str(task)
    normalized = _normalize_status(status)
    with _lock:
        state = _windows.get(name)
        if state is None:
            return {
                "window": name,
                "task": task_name,
                "updated": False,
                "reason": "window_not_open",
            }
        state["tasks"][task_name] = {
            "done": normalized == "completed",
            "status": normalized,
        }
    return {
        "window": name,
        "task": task_name,
        "updated": True,
        "done": normalized == "completed",
    }


finish_task = complete_task
mark_task = complete_task


# ---------------------------------------------------------------------------
# 结果构造
# ---------------------------------------------------------------------------
def _result(
    name,
    *,
    opened,
    waited=False,
    catch_up=False,
    concurrency=0,
    skipped=None,
    reason=None,
    group=None,
    alerts=None,
    pending=None,
    failed=None,
    wait_seconds=0.0,
    catch_up_reason=None,
):
    return {
        "window": name,
        "opened": opened,
        "waited": waited,
        "catch_up": catch_up,
        "concurrency": concurrency,
        "skipped": (not opened) if skipped is None else skipped,
        "reason": reason,
        "group": group,
        "wait_seconds": round(wait_seconds, 3),
        "pending_upstream": list(pending or []),
        "failed_upstream": list(failed or []),
        "alerts": list(alerts or []),
        "catch_up_reason": catch_up_reason,
    }


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def open(window):
    """打开窗口：校验上游 → 处理错过补跑 → 受资源组并发上限约束。"""
    spec = dict(window) if isinstance(window, dict) else {"name": str(window)}
    name = _name(spec)
    group = _group(spec)
    limit = _limit(spec)

    with _lock:
        active = _groups.setdefault(group, set())
        concurrency = len(active)
        existing = _windows.get(name)
        if existing is not None and existing.get("status") == "open":
            # 幂等：同一窗口重复 open 不产生第二次补跑
            return _result(
                name,
                opened=True,
                waited=False,
                catch_up=False,
                concurrency=len(_groups.get(existing.get("group"), set())),
                reason="already_open",
                group=existing.get("group"),
            )

    missed, missed_reason = _missed(spec)
    if missed and _catch_up_policy(spec) == "never":
        return _result(
            name,
            opened=False,
            reason="missed_window",
            group=group,
            concurrency=concurrency,
            alerts=["窗口已错过：%s" % missed_reason],
        )

    dependency = _validate_upstreams(spec)
    if dependency["status"] == "failed":
        return _result(
            name,
            opened=False,
            waited=dependency["waited"],
            catch_up=False,
            concurrency=concurrency,
            reason="upstream_failed",
            group=group,
            alerts=dependency["alerts"],
            pending=dependency["pending"],
            failed=dependency["failed"],
            wait_seconds=dependency["wait_seconds"],
            catch_up_reason=None,
        )
    if dependency["status"] == "pending":
        return _result(
            name,
            opened=False,
            waited=dependency["waited"],
            catch_up=False,
            concurrency=concurrency,
            skipped=True,
            reason=dependency["reason"],
            group=group,
            alerts=dependency["alerts"],
            pending=dependency["pending"],
            wait_seconds=dependency["wait_seconds"],
            catch_up_reason=None,
        )

    waited = dependency["waited"]
    timeout = _wait_timeout(spec)
    slot_start = _clock()
    slot_deadline = slot_start + timeout
    policy = _concurrency_policy(spec)

    while True:
        with _lock:
            active = _groups.setdefault(group, set())
            concurrency = len(active)

            # 幂等与补跑去重在同一把锁内复查，避免并发 open 重复补跑
            existing = _windows.get(name)
            if existing is not None and existing.get("status") == "open":
                return _result(
                    name,
                    opened=True,
                    waited=waited,
                    catch_up=False,
                    concurrency=len(_groups.get(existing.get("group"), set())),
                    reason="already_open",
                    group=existing.get("group"),
                )
            if missed:
                catch_up_key = _catch_up_key(name, spec)
                if catch_up_key in _catch_up_done:
                    return _result(
                        name,
                        opened=False,
                        waited=dependency["waited"],
                        concurrency=concurrency,
                        reason="catch_up_exhausted",
                        group=group,
                        alerts=["错过窗口的补跑已执行过：%s" % missed_reason],
                        wait_seconds=dependency["wait_seconds"],
                    )
            else:
                catch_up_key = None

            slot_free = limit is None or len(active) < limit
            if slot_free:
                slot_elapsed = _clock() - slot_start
                total_wait = dependency["wait_seconds"] + slot_elapsed
                active.add(name)
                _windows[name] = {
                    "name": name,
                    "group": group,
                    "status": "open",
                    "opened_at": _clock(),
                    "closed_at": None,
                    "catch_up": bool(missed),
                    "catch_up_reason": (
                        "错过窗口补跑：%s" % missed_reason if missed else None
                    ),
                    "waited": waited,
                    "wait_seconds": total_wait,
                    "tasks": _task_state(spec),
                    "aborted": [],
                    "alerts": list(dependency["alerts"]),
                }
                if missed and catch_up_key is not None:
                    _catch_up_done.add(catch_up_key)
                return _result(
                    name,
                    opened=True,
                    waited=waited,
                    catch_up=bool(missed),
                    concurrency=len(active),
                    reason=None,
                    group=group,
                    alerts=dependency["alerts"],
                    pending=dependency["pending"],
                    failed=dependency["failed"],
                    wait_seconds=total_wait,
                    catch_up_reason=(
                        "错过窗口补跑：%s" % missed_reason if missed else None
                    ),
                )

            policy = _concurrency_policy(spec)
            elapsed = _clock() - slot_start

        if policy != "wait":
            return _result(
                name,
                opened=False,
                waited=waited,
                concurrency=concurrency,
                skipped=True,
                reason="concurrency_limit",
                group=group,
                alerts=["资源组 %s 并发已达上限 %s" % (group, limit)],
                wait_seconds=dependency["wait_seconds"],
            )
        if elapsed >= timeout:
            return _result(
                name,
                opened=False,
                waited=True,
                concurrency=concurrency,
                skipped=True,
                reason="concurrency_timeout",
                group=group,
                alerts=["等待资源组 %s 的并发槽位超时（%ss）" % (group, timeout)],
                wait_seconds=dependency["wait_seconds"] + elapsed,
            )
        waited = True
        _sleep(min(_POLL_INTERVAL, max(0.0, slot_deadline - _clock())))


def close(window):
    """关闭窗口：清理未完成任务（记入 aborted），释放资源组并发槽位。"""
    spec = dict(window) if isinstance(window, dict) else {"name": str(window)}
    name = _name(spec)

    with _lock:
        state = _windows.get(name)
        if state is not None:
            group = state.get("group", _group(spec))
            _groups.get(group, set()).discard(name)
            if state.get("status") == "closed":
                # 重复 close 保持幂等，返回首次关闭时的清理结果
                return {
                    "window": name,
                    "closed": True,
                    "aborted": list(state.get("aborted", [])),
                    "cleaned_tasks": list(state.get("aborted", [])),
                    "group": group,
                    "concurrency": len(_groups.get(group, set())),
                }
            aborted = []
            for task_name, info in state.get("tasks", {}).items():
                if not info.get("done"):
                    info["status"] = "aborted"
                    info["cleaned"] = True
                    aborted.append(task_name)
            state["status"] = "closed"
            state["closed_at"] = _clock()
            state["aborted"] = list(aborted)
        else:
            group = _group(spec)
            aborted = [
                task_name
                for task_name, info in _task_state(spec).items()
                if not info.get("done")
            ]
            _groups.get(group, set()).discard(name)

        concurrency = len(_groups.get(group, set()))
        return {
            "window": name,
            "closed": True,
            "aborted": aborted,
            "cleaned_tasks": aborted,
            "group": group,
            "concurrency": concurrency,
        }


def status(window):
    """查询窗口状态（open/closed/unknown）、并发、补跑与任务情况。"""
    spec = dict(window) if isinstance(window, dict) else {"name": str(window)}
    name = _name(spec)
    with _lock:
        state = _windows.get(name)
        if state is None:
            group = _group(spec)
            return {
                "window": name,
                "status": "unknown",
                "state": "unknown",
                "opened": False,
                "catch_up": False,
                "catch_up_reason": None,
                "group": group,
                "concurrency": len(_groups.get(group, set())),
                "opened_at": None,
                "closed_at": None,
                "tasks": {},
                "aborted": [],
            }
        group = state.get("group", "default")
        tasks = {
            task_name: {
                "done": bool(info.get("done")),
                "status": info.get("status"),
                "cleaned": bool(info.get("cleaned")),
            }
            for task_name, info in state.get("tasks", {}).items()
        }
        return {
            "window": name,
            "status": state.get("status"),
            "state": state.get("status"),
            "opened": state.get("status") == "open",
            "catch_up": bool(state.get("catch_up")),
            "catch_up_reason": state.get("catch_up_reason"),
            "group": group,
            "concurrency": len(_groups.get(group, set())),
            "opened_at": state.get("opened_at"),
            "closed_at": state.get("closed_at"),
            "tasks": tasks,
            "aborted": list(state.get("aborted", [])),
        }


# 两种常见命名都提供
state = status


def snapshot():
    """整体快照：所有窗口状态、资源组占用与上限、已补跑窗口。"""
    with _lock:
        return {
            "windows": {
                name: status({"name": name}) for name in sorted(_windows)
            },
            "groups": {
                group: {
                    "active": sorted(active),
                    "limit": _group_limits.get(group),
                }
                for group, active in sorted(_groups.items())
            },
            "catch_up_done": sorted(_catch_up_done),
        }
