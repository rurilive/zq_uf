"""按站点共享的采集退避。

同站点的所有采集任务（含跨进程任务）共享一个退避窗口和请求预算：
任一任务命中 429，全站进入退避；窗口结束后按阶梯预算恢复，连续
成功才能回到满速，期间再次命中 429 会重新退避并加长窗口。

状态落盘到 JSON 文件，跨进程通过 fcntl 文件锁同步；进程重启后
继续尚未结束的退避窗口。公共接口保持 signal(site, status) 与
budget(site) 两个函数。
"""

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager

# 满速时每个窗口的请求预算。
FULL_BUDGET = 10
# 退避窗口内允许的试探预算（命中 429 后剩余预算即此值）。
BACKOFF_BUDGET = 3
# 恢复阶梯预算：窗口结束后先到 level 1（3），连续成功后升 level 2（6），
# 再达标即回到 level 3 的满速预算（FULL_BUDGET）。
RECOVERY_BUDGETS = (3, 6)
# 每升一级需要的连续成功次数；中途任何 429 都会清零。
SUCCESSES_PER_STEP = 3
# 首次退避窗口秒数，重复命中 429 时翻倍，上限 WINDOW_CAP。
WINDOW_BASE = 5.0
WINDOW_CAP = 300.0

TOP_LEVEL = len(RECOVERY_BUDGETS) + 1  # normal 所在 level

_STATE_FILENAME = "state.json"
_LOCK_FILENAME = "state.lock"

# 进程内缓存，仅作检视用途，磁盘文件始终是权威状态。
# 保留模块级 _state 以兼容外部的 backoff._state.clear() 用法。
_state = {}


def _default_state_dir():
    env = os.environ.get("CRAWLBACKOFF_HOME")
    if env:
        return env
    home = os.path.expanduser("~")
    if home and os.path.isdir(home):
        return os.path.join(home, ".cache", "crawlbackoff")
    return os.path.join(tempfile.gettempdir(), "crawlbackoff")


_state_dir = _default_state_dir()


def set_state_dir(path):
    """切换状态目录（测试/演示隔离用），并清空进程内缓存。"""
    global _state_dir
    _state_dir = os.fspath(path)
    _state.clear()


def _now():
    """当前时间，单独成函数便于测试注入时钟。"""
    return time.time()


@contextmanager
def _locked():
    os.makedirs(_state_dir, exist_ok=True)
    lock_path = os.path.join(_state_dir, _LOCK_FILENAME)
    with open(lock_path, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _state_path():
    return os.path.join(_state_dir, _STATE_FILENAME)


def _load():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        # 文件损坏时按空状态处理，下次写入会覆盖修复。
        return {}
    return data if isinstance(data, dict) else {}


def _save(data):
    fd, tmp = tempfile.mkstemp(prefix=".state.", dir=_state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _state_path())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _level_budget(level):
    """各恢复级别对应的窗口预算。"""
    if level <= 0:
        return BACKOFF_BUDGET
    if level < TOP_LEVEL:
        return RECOVERY_BUDGETS[level - 1]
    return FULL_BUDGET


def _fresh(now):
    return {
        "status": "ok",
        "phase": "normal",
        "level": TOP_LEVEL,
        "remaining": FULL_BUDGET,
        "window_end": 0.0,
        "window_seconds": WINDOW_BASE,
        "consecutive_successes": 0,
        "backoff_count": 0,
        "updated_at": now,
    }


def _reconcile(s, now):
    """按当前时间惰性推进状态：退避窗口结束则进入恢复阶梯第一级。"""
    if s["window_end"] and now >= s["window_end"]:
        if s["phase"] == "backoff":
            # 退避窗口结束：全站进入恢复阶梯第一级，不直接回满速。
            s["phase"] = "recovery"
            s["level"] = 1
            s["consecutive_successes"] = 0
        s["remaining"] = _level_budget(s["level"])
        s["window_end"] = now + s["window_seconds"]


def _enter_backoff(s, now):
    s["backoff_count"] = s.get("backoff_count", 0) + 1
    s["window_seconds"] = min(
        WINDOW_CAP, WINDOW_BASE * (2 ** (s["backoff_count"] - 1))
    )
    s["phase"] = "backoff"
    s["level"] = 0
    s["remaining"] = BACKOFF_BUDGET
    s["consecutive_successes"] = 0
    s["window_end"] = now + s["window_seconds"]
    s["status"] = 429
    s["updated_at"] = now


def _record_success(s, now, status):
    _reconcile(s, now)
    s["status"] = status
    if s["phase"] == "backoff":
        # 退避窗口内只放行试探流量，不累计恢复进度。
        s["remaining"] = max(0, s["remaining"] - 1)
    elif s["phase"] == "recovery":
        s["remaining"] = max(0, s["remaining"] - 1)
        s["consecutive_successes"] += 1
        if s["consecutive_successes"] >= SUCCESSES_PER_STEP:
            # 连续成功达标：升一级并按新级别补充预算。
            s["level"] += 1
            s["consecutive_successes"] = 0
            if s["level"] >= TOP_LEVEL:
                s["phase"] = "normal"
                s["level"] = TOP_LEVEL
            s["remaining"] = _level_budget(s["level"])
            s["window_end"] = now + s["window_seconds"]
        elif s["remaining"] <= 0:
            # 本窗口预算用完但还未升级：按当前级别限流续窗口。
            s["remaining"] = _level_budget(s["level"])
            s["window_end"] = now + s["window_seconds"]
    else:  # normal
        s["remaining"] = max(0, s["remaining"] - 1)
        if s["remaining"] <= 0:
            s["remaining"] = FULL_BUDGET
            s["window_end"] = now + s["window_seconds"]
    s["updated_at"] = now


def _snapshot(site, s, now):
    if s is None:
        return {
            "status": "ok",
            "phase": "normal",
            "level": TOP_LEVEL,
            "remaining": FULL_BUDGET,
            "budget": FULL_BUDGET,
            "window_end": 0.0,
            "window_seconds": 0.0,
            "window_remaining": 0.0,
            "consecutive_successes": 0,
        }
    return {
        "site": site,
        "status": s["status"],
        "phase": s["phase"],
        "level": s["level"],
        "remaining": s["remaining"],
        "budget": _level_budget(s["level"]),
        "window_end": s["window_end"],
        "window_seconds": s["window_seconds"],
        "window_remaining": round(max(0.0, s["window_end"] - now), 3),
        "consecutive_successes": s["consecutive_successes"],
    }


def signal(site, status):
    """上报一次请求结果。429 触发全站退避，其他状态视为成功。

    返回该站点最新的窗口/预算/恢复阶段快照。
    """
    site = str(site)
    with _locked():
        data = _load()
        now = _now()
        s = data.get(site)
        if s is None:
            s = _fresh(now)
            data[site] = s
        if status == 429:
            _enter_backoff(s, now)
        else:
            _record_success(s, now, status)
        _save(data)
        _state[site] = dict(s)
        return _snapshot(site, s, now)


def budget(site):
    """返回站点当前窗口、剩余预算与恢复阶段（不落盘新站点）。"""
    site = str(site)
    with _locked():
        data = _load()
        now = _now()
        s = data.get(site)
        if s is None:
            return _snapshot(site, None, now)
        before = (s["phase"], s["level"], s["remaining"], s["window_end"])
        _reconcile(s, now)
        after = (s["phase"], s["level"], s["remaining"], s["window_end"])
        if before != after:
            _save(data)
        _state[site] = dict(s)
        return _snapshot(site, s, now)
