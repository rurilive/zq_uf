"""任务调度：租户配额 + 优先级 + 低优保底 + 抢占续跑。

默认配置（无配额、无保底、容量不限）退化为先进先出。
口径详见 README.md。
"""

import json
import time
from collections import defaultdict

_queue = []               # 待调度任务
_running = {}             # job_id -> 运行中任务
_usage = defaultdict(list)  # tenant -> 窗口内出队时间戳
_released = set()         # 已释放的 job_id（幂等）
_config = {}
_stats = {"preemptions": 0}
_preempt_log = []         # 抢占记录（含被抢占任务进度）
_wait_log = []            # (priority, 等待秒数)
_events = []              # 留档事件流
_clock = time.monotonic
_seq = 0

_DEFAULTS = {
    "capacity": None,        # 并发执行位，None 不限
    "window_seconds": 60.0,  # 配额时间窗
    "quotas": {},            # tenant -> 窗口内最多出队次数
    "guarantees": {},        # priority -> 保底并发数（该档不被抢占跌破）
}


def reset():
    """清空全部状态并恢复默认配置（测试用）。"""
    global _clock, _seq
    _queue.clear()
    _running.clear()
    _usage.clear()
    _released.clear()
    _config.clear()
    _config.update(_DEFAULTS)
    _stats["preemptions"] = 0
    _preempt_log.clear()
    _wait_log.clear()
    _events.clear()
    _clock = time.monotonic
    _seq = 0


reset()


def configure(**kw):
    """更新配置：capacity / window_seconds / quotas / guarantees。"""
    unknown = set(kw) - set(_DEFAULTS)
    if unknown:
        raise ValueError("未知配置项: %s" % sorted(unknown))
    _config.update(kw)


def set_clock(fn):
    """注入时钟（测试时间窗用），fn() 返回单调秒数。"""
    global _clock
    _clock = fn


def _log(type_, **kw):
    _events.append(dict({"seq": _seq, "at": _clock(), "type": type_}, **kw))


def submit(job):
    """入队任务，返回当前待调度任务数。

    可带字段：id（缺省自动生成）、tenant（缺省 default）、
    priority（越大越优先，缺省 0）、progress（续跑进度 0..1）。
    """
    global _seq
    j = dict(job)
    j.setdefault("id", "auto-%d" % _seq)
    j.setdefault("tenant", "default")
    j.setdefault("priority", 0)
    j.setdefault("progress", 0.0)
    j["_seq"] = _seq
    j["_submitted"] = _clock()
    _seq += 1
    _queue.append(j)
    _log("submit", job_id=j["id"], tenant=j["tenant"], priority=j["priority"])
    return len(_queue)


def _prune(now):
    """回收窗口外配额记录，返回回收条数（幂等，可反复调用）。"""
    win = _config["window_seconds"]
    n = 0
    for tenant in list(_usage):
        kept = [t for t in _usage[tenant] if now - t < win]
        n += len(_usage[tenant]) - len(kept)
        if kept:
            _usage[tenant] = kept
        else:
            del _usage[tenant]
    return n


def reclaim(tenant):
    """回收租户窗口外配额使用量，幂等；返回本次回收条数。"""
    before = sum(len(v) for v in _usage.values())
    _prune(_clock())
    n = before - sum(len(v) for v in _usage.values())
    _log("reclaim", tenant=tenant, reclaimed=n,
         used=len(_usage.get(tenant, [])))
    return n


def _quota_ok(job):
    limit = _config["quotas"].get(job["tenant"])
    return limit is None or len(_usage.get(job["tenant"], [])) < limit


def _pick(candidates):
    """先补保底档（低优先级优先），再按优先级+等待时间选。"""
    for p in sorted(_config["guarantees"]):
        running = sum(1 for j in _running.values() if j["priority"] == p)
        if running < _config["guarantees"][p]:
            cands = [j for j in candidates if j["priority"] == p]
            if cands:
                return min(cands, key=lambda j: j["_seq"])
    return max(candidates, key=lambda j: (j["priority"], -j["_seq"]))


def _public(j):
    return {k: v for k, v in j.items() if not k.startswith("_")}


def _issue(job, now):
    _queue.remove(job)
    _usage[job["tenant"]].append(now)
    _running[job["id"]] = job
    _wait_log.append((job["priority"], now - job["_submitted"]))
    _log("issue", job_id=job["id"], tenant=job["tenant"],
         priority=job["priority"], progress=job["progress"])
    return _public(job)


def _preempt(victim, by_job, now):
    """抢占：保存被抢占任务进度并回队，等待时间从原始提交时刻算。"""
    del _running[victim["id"]]
    _queue.append(victim)
    _stats["preemptions"] += 1
    rec = {"job_id": victim["id"], "tenant": victim["tenant"],
           "priority": victim["priority"], "progress": victim["progress"],
           "preempted_by": by_job["id"], "at": now}
    _preempt_log.append(rec)
    _log("preempt", **rec)


def next_job():
    """按配额/保底/优先级出队下一个任务；无可出队任务返回 None。

    执行位已满且存在更高优先级待跑任务时，抢占优先级最低的
    运行中任务（保底档不跌破），被抢占任务带进度回队待续跑。
    """
    now = _clock()
    _prune(now)
    candidates = [j for j in _queue if _quota_ok(j)]
    if not candidates:
        return None
    best = _pick(candidates)
    cap = _config["capacity"]
    if cap is None or len(_running) < cap:
        return _issue(best, now)
    floor = _config["guarantees"]
    victims = [
        j for j in _running.values()
        if sum(1 for r in _running.values() if r["priority"] == j["priority"])
        > floor.get(j["priority"], 0)
    ]
    victim = min(victims, key=lambda j: (j["priority"], j["progress"]),
                 default=None)
    if victim is None or best["priority"] <= victim["priority"]:
        return None
    _preempt(victim, best, now)
    return _issue(best, now)


def report_progress(job_id, progress):
    """上报运行中任务进度（0..1），供抢占时保存。"""
    if job_id in _running:
        _running[job_id]["progress"] = progress
        return True
    return False


def release(job_id):
    """释放执行位与配额占用，幂等：重复释放返回 False 且不重复记录。"""
    if job_id in _released:
        return False
    job = _running.pop(job_id, None)
    if job is None:
        return False
    _released.add(job_id)
    _log("release", job_id=job_id, tenant=job["tenant"],
         used=len(_usage.get(job["tenant"], [])))
    return True


def stats():
    """配额使用、抢占与等待统计。"""
    _prune(_clock())
    wait_by_pri = defaultdict(list)
    for p, w in _wait_log:
        wait_by_pri[p].append(w)
    waits = [w for _, w in _wait_log]
    return {
        "quota": {t: {"used": len(_usage.get(t, [])),
                      "limit": _config["quotas"].get(t),
                      "window_seconds": _config["window_seconds"]}
                  for t in set(_config["quotas"]) | set(_usage)},
        "preemptions": {"count": _stats["preemptions"],
                        "records": list(_preempt_log)},
        "wait": {
            "count": len(waits),
            "avg": sum(waits) / len(waits) if waits else 0.0,
            "max": max(waits) if waits else 0.0,
            "by_priority": {p: {"count": len(ws), "avg": sum(ws) / len(ws)}
                            for p, ws in sorted(wait_by_pri.items())},
        },
        "running": len(_running),
        "pending": len(_queue),
    }


def events():
    """留档事件流（submit/issue/preempt/release/reclaim）。"""
    return list(_events)


def dump_log(path):
    """把留档事件流写成 JSONL 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        for e in _events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(_events)
