"""迟到指标处理：按事件时间归窗，整窗重算，先撤销再写，幂等并保留版本。"""

import threading

_lock = threading.RLock()

# window -> {"events": [...], "current": 当前版本 | None, "history": [版本, ...]}
_windows = {}


def _window_of(metric):
    """窗口按事件时间取（小时粒度）；没有事件时间才用调用方给的 window。"""
    event_time = metric.get("event_time")
    if event_time:
        return str(event_time)[:13]
    return metric.get("window", "")


def _aggregate(events):
    """从事件日志整窗重算。失败抛异常，由调用方保证旧版本不动。"""
    count = 0
    total = 0.0
    for e in events:
        count += 1
        total += float(e.get("value", 0))
    return {"count": count, "sum": total}


def _commit_recompute(window, reason):
    """整窗重算：算出新值后，在同一临界区内撤销旧版本、写入新版本。

    撤销和写入要么一起成功要么一起回退：计算失败时抛异常，
    旧版本保持不动（回到上一版本）。
    """
    with _lock:
        state = _windows.setdefault(
            window, {"events": [], "current": None, "history": []})
        new_result = _aggregate(state["events"])  # 失败则状态不变
        retracted = None
        if state["current"] is not None:
            retracted = {
                "version": state["current"]["version"],
                "count": state["current"]["count"],
                "sum": state["current"]["sum"],
            }
        record = {
            "version": len(state["history"]) + 1,
            "window": window,
            "count": new_result["count"],
            "sum": new_result["sum"],
            "retracted": retracted,
            "reason": reason,
        }
        state["current"] = record
        state["history"].append(record)
        return record


def _response(record, recomputed):
    return {
        "window": record["window"],
        "recomputed": recomputed,
        "count": record["count"],
        "sum": record["sum"],
        "retracted": record["retracted"],
        "version": record["version"],
    }


def add(metric):
    """事件进入其事件时间所在窗口；窗口已有结果则触发整窗重算。"""
    window = _window_of(metric)
    float(metric.get("value", 0))  # 非法值直接拒绝，不进事件日志
    with _lock:
        state = _windows.setdefault(
            window, {"events": [], "current": None, "history": []})
        state["events"].append(dict(metric))
        had_result = state["current"] is not None
    record = _commit_recompute(
        window, reason="late-event" if had_result else "initial")
    return _response(record, recomputed=had_result)


def recompute(window):
    """整窗重算：先撤销旧结果再写新结果；重复重算结果一致。"""
    record = _commit_recompute(window, reason="manual-recompute")
    return _response(record, recomputed=True)


def versions(window):
    """窗口的版本历史，含每次重算撤销的旧值明细。"""
    state = _windows.get(window)
    if state is None:
        return []
    with _lock:
        return [dict(r) for r in state["history"]]


def reset():
    """清空全部状态（测试用）。"""
    with _lock:
        _windows.clear()
