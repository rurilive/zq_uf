"""采集任务配额：按站点分配预算，支持日额度与突发额度，多进程共享原子扣减。"""

import json
import os
import tempfile
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

LIMIT = 1000  # 全局日安全上限：所有站点合计每天最多消耗的请求数

_DEFAULT_WINDOW = 1.0  # 突发令牌桶默认窗口（秒）


class BudgetError(RuntimeError):
    """全局额度耗尽、请求本身不可能满足，或状态文件损坏。"""


_configs = {}


def _state_path():
    return os.environ.get("CRAWLBUDGET_STATE") or os.path.join(
        tempfile.gettempdir(), "crawlbudget_state.json"
    )


def _default_config():
    return {"daily": LIMIT, "burst": None, "window": _DEFAULT_WINDOW}


def _config_for(site):
    return _configs.get(site) or _default_config()


def _check_site(site):
    if not isinstance(site, str) or not site.strip():
        raise ValueError("site must be a non-empty string, got %r" % (site,))


def _check_n(n):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n must be a positive int, got %r" % (n,))


def configure(site, daily=None, burst=None, window=None):
    """为站点单独配置预算；未配置的字段使用默认值。

    daily: 站点日额度（正整数，默认 LIMIT）；burst: 突发窗口内允许的请求数
    （默认 None 不限）；window: 突发窗口秒数（默认 1.0）。多进程使用时每个
    进程启动后都需调用。
    """
    _check_site(site)
    cfg = _default_config()
    if daily is not None:
        if isinstance(daily, bool) or not isinstance(daily, int) or daily < 1:
            raise ValueError("daily must be a positive int, got %r" % (daily,))
        cfg["daily"] = daily
    if burst is not None:
        if isinstance(burst, bool) or not isinstance(burst, (int, float)) or burst < 1:
            raise ValueError("burst must be a positive number, got %r" % (burst,))
        cfg["burst"] = burst
    if window is not None:
        if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
            raise ValueError("window must be a positive number, got %r" % (window,))
        cfg["window"] = float(window)
    _configs[site] = cfg


def _today():
    return time.strftime("%Y-%m-%d")


def _fresh_state():
    return {"day": _today(), "global_spent": 0, "global_rejected": 0, "sites": {}}


def _site_state(state, site):
    return state["sites"].setdefault(
        site, {"spent": 0, "rejected": 0, "tokens": None, "updated": None}
    )


def _rollover(state):
    if state.get("day") != _today():
        state.clear()
        state.update(_fresh_state())


def _wait_next_day():
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return float(max(0.0, midnight - now))


def _lock(f):
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    else:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)


def _unlock(f):
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    else:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _open_state():
    path = _state_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        return open(path, "r+b")
    except FileNotFoundError:
        return open(path, "w+b")


def _parse(raw, path):
    if not raw.strip():
        return _fresh_state()
    try:
        state = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BudgetError("state file corrupted: %s: %s" % (path, exc)) from exc
    if not isinstance(state, dict) or not isinstance(state.get("sites"), dict):
        raise BudgetError("state file has unexpected shape: %s" % path)
    return state


def _transact(mutator):
    """在独占文件锁内读-改-写共享状态，保证多进程原子扣减。"""
    with _open_state() as f:
        _lock(f)
        try:
            f.seek(0)
            state = _parse(f.read(), f.name)
            _rollover(state)
            try:
                result = mutator(state)
            finally:
                f.seek(0)
                f.truncate()
                f.write(json.dumps(state).encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            return result
        finally:
            _unlock(f)


def _burst_wait(site_state, cfg, n):
    """令牌桶：可立即扣减则扣减并返回 None，否则返回需等待的秒数。"""
    now = time.monotonic()
    capacity = float(cfg["burst"])
    rate = capacity / cfg["window"]
    tokens = site_state["tokens"]
    updated = site_state["updated"]
    if tokens is None or updated is None:
        tokens = capacity
    else:
        tokens = min(capacity, tokens + (now - updated) * rate)
    if tokens >= n:
        site_state["tokens"] = tokens - n
        site_state["updated"] = now
        return None
    site_state["tokens"] = tokens
    site_state["updated"] = now
    return float((n - tokens) / rate)


def consume(site, n=1):
    """扣减站点预算。

    成功返回该站点当日已消耗量（int）；站点日额度或突发额度不足时不扣减，
    返回需要等待的秒数（float）；全局安全上限耗尽或请求本身不可能满足时
    抛 BudgetError。
    """
    _check_site(site)
    _check_n(n)
    cfg = _config_for(site)
    if n > cfg["daily"]:
        raise BudgetError(
            "%s: n=%d exceeds daily budget %d, can never be fulfilled"
            % (site, n, cfg["daily"])
        )
    if cfg["burst"] is not None and n > cfg["burst"]:
        raise BudgetError(
            "%s: n=%d exceeds burst capacity %s, can never be fulfilled"
            % (site, n, cfg["burst"])
        )
    if n > LIMIT:
        raise BudgetError("n=%d exceeds global LIMIT %d" % (n, LIMIT))

    def mut(state):
        entry = _site_state(state, site)
        if state["global_spent"] + n > LIMIT:
            entry["rejected"] += 1
            state["global_rejected"] += 1
            raise BudgetError(
                "global daily budget exhausted (%d), retry after %.0fs"
                % (LIMIT, _wait_next_day())
            )
        if entry["spent"] + n > cfg["daily"]:
            entry["rejected"] += 1
            return _wait_next_day()
        if cfg["burst"] is not None:
            wait = _burst_wait(entry, cfg, n)
            if wait is not None:
                entry["rejected"] += 1
                return wait
        entry["spent"] += n
        state["global_spent"] += n
        return entry["spent"]

    return _transact(mut)


def remaining(site=None):
    """当日剩余额度：站点日剩余与全局剩余的较小值；site 为 None 时只看全局。"""
    if site is not None:
        _check_site(site)
    cfg = _config_for(site) if site is not None else None

    def mut(state):
        global_left = max(0, LIMIT - state["global_spent"])
        if site is None:
            return global_left
        entry = state["sites"].get(site)
        spent = entry["spent"] if entry else 0
        return max(0, min(cfg["daily"] - spent, global_left))

    return _transact(mut)


def stats(site=None):
    """消耗、剩余和拒绝次数。site 为 None 时返回全局与所有已知站点。"""
    if site is not None:
        _check_site(site)

    def mut(state):
        def row(name):
            entry = state["sites"].get(name) or {"spent": 0, "rejected": 0}
            cfg = _config_for(name)
            global_left = max(0, LIMIT - state["global_spent"])
            return {
                "spent": entry["spent"],
                "remaining": max(0, min(cfg["daily"] - entry["spent"], global_left)),
                "rejected": entry["rejected"],
            }

        names = {site} if site is not None else set(state["sites"]) | set(_configs)
        return {
            "global": {
                "spent": state["global_spent"],
                "remaining": max(0, LIMIT - state["global_spent"]),
                "rejected": state["global_rejected"],
            },
            "sites": {name: row(name) for name in sorted(names)},
        }

    return _transact(mut)


def reset():
    """清空共享计数并恢复默认配置（测试用）。"""
    _configs.clear()
    with _open_state() as f:
        _lock(f)
        try:
            f.seek(0)
            f.truncate()
            f.write(json.dumps(_fresh_state()).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock(f)
