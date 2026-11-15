"""出口熔断：连续失败熔断，低频探测，连续成功阶梯恢复，状态跨进程共享。

状态机：closed → open → half_open（阶梯恢复）→ closed；恢复失败回到 open。
"""

import time

from . import store

CONFIG = {
    "failure_threshold": 3,       # 连续失败达到阈值才熔断
    "probe_interval_sec": 60,     # 熔断后低频探测间隔
    "recovery_stages": 2,         # 恢复阶梯数，逐级放量，不一次放满
    "successes_per_stage": 2,     # 每级所需连续成功次数
    "low_traffic_hours": (0, 6),  # 低流量窗口（UTC 小时），只在窗口内探测/恢复；None 表示不限制
}


def _now():
    return time.time()


def _default_record(proxy):
    return {
        "proxy": proxy,
        "state": "closed",
        "failures": 0,
        "successes": 0,
        "recovery_stage": 0,
        "inflight": 0,
        "version": 0,
        "opened_at": None,
        "next_probe_at": None,
        "last_probe": None,
    }


def _get(data, proxy):
    return data.setdefault(proxy, _default_record(proxy))


def _low_traffic(now):
    window = CONFIG["low_traffic_hours"]
    if window is None:
        return True
    start, end = window
    hour = time.gmtime(now).tm_hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _audit(rec, event, **extra):
    entry = {
        "proxy": rec["proxy"],
        "event": event,
        "state": rec["state"],
        "failures": rec["failures"],
        "recovery_stage": rec["recovery_stage"],
        "version": rec["version"],
        "at": _now(),
    }
    entry.update(extra)
    store.append_audit(entry)


def _view(rec, **extra):
    out = {
        "proxy": rec["proxy"],
        "state": rec["state"],
        "failures": rec["failures"],
        "recovery_stage": rec["recovery_stage"],
        "version": rec["version"],
        "last_probe": rec["last_probe"],
    }
    out.update(extra)
    return out


def use(proxy):
    """选择出口：熔断期不派新任务；到点且在低流量窗口时转入半开放行探测；
    半开恢复期按阶梯（recovery_stage）限制在途并发，逐步放量。"""
    now = _now()
    with store.locked() as data:
        rec = _get(data, proxy)
        if rec["state"] == "open":
            if now >= rec["next_probe_at"] and _low_traffic(now):
                rec["state"] = "half_open"
                rec["recovery_stage"] = 1
                rec["successes"] = 0
                rec["inflight"] = 0
                rec["version"] += 1
                _audit(rec, "probe_start")
            else:
                return _view(rec, usable=False, probe=False)
        if rec["state"] == "half_open":
            if rec["inflight"] >= rec["recovery_stage"]:
                return _view(rec, usable=False, probe=False)
            rec["inflight"] += 1
            rec["version"] += 1
            return _view(rec, usable=True, probe=True)
        return _view(rec, usable=True, probe=False)


def report(proxy, ok):
    """上报结果：closed 累计连续失败，到阈值熔断；half_open 连续成功逐级恢复，
    任一失败立即回到熔断。状态变化写审计。"""
    now = _now()
    with store.locked() as data:
        rec = _get(data, proxy)
        was_probe = rec["state"] == "half_open"
        tripped = False
        probe_result = None
        if rec["inflight"] > 0:
            rec["inflight"] -= 1
        if rec["state"] == "closed":
            if ok:
                rec["failures"] = 0
            else:
                rec["failures"] += 1
                if rec["failures"] >= CONFIG["failure_threshold"]:
                    rec["state"] = "open"
                    rec["opened_at"] = now
                    rec["next_probe_at"] = now + CONFIG["probe_interval_sec"]
                    rec["recovery_stage"] = 0
                    tripped = True
                    _audit(rec, "tripped")
        elif rec["state"] == "half_open":
            rec["last_probe"] = {"at": now, "ok": ok}
            probe_result = "success" if ok else "failed"
            if ok:
                rec["successes"] += 1
                if rec["successes"] >= CONFIG["successes_per_stage"]:
                    if rec["recovery_stage"] < CONFIG["recovery_stages"]:
                        rec["recovery_stage"] += 1
                        rec["successes"] = 0
                        _audit(rec, "stage_up")
                    else:
                        rec["state"] = "closed"
                        rec["failures"] = 0
                        rec["successes"] = 0
                        rec["recovery_stage"] = 0
                        rec["next_probe_at"] = None
                        _audit(rec, "recovered")
            else:
                # 恢复失败回到熔断，等待下一轮低频探测
                rec["state"] = "open"
                rec["opened_at"] = now
                rec["next_probe_at"] = now + CONFIG["probe_interval_sec"]
                rec["recovery_stage"] = 0
                rec["successes"] = 0
                _audit(rec, "recovery_failed")
        rec["version"] += 1
        return _view(rec, ok=ok, tripped=tripped, probe=was_probe,
                     probe_result=probe_result)


def status(proxy):
    """当前状态快照：状态、失败次数、探测结果、恢复阶段、状态版本。"""
    with store.locked() as data:
        rec = _get(data, proxy)
        return _view(rec, usable=rec["state"] != "open", inflight=rec["inflight"])


def history(proxy):
    """按出口查询历史状态变化（审计日志）。"""
    return [e for e in store.read_audit() if e["proxy"] == proxy]
