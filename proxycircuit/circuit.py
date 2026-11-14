"""按 (站点, 出口) 维度的三态熔断器：closed -> open -> half_open。

- closed：连续失败达到 FAILURE_THRESHOLD 进入 open。
- open：冷却 COOLDOWN_SECONDS 后，下一次 acquire 只放行一个探针进入 half_open。
- half_open：探针成功回到 closed 并清零失败计数；失败回到 open 并重新计时。
- 状态按 (site, proxy) 隔离，站点之间互不影响。
"""

import time

PROXIES = ["10.0.0.1:8080", "10.0.0.2:8080", "10.0.0.3:8080"]

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 30.0

_now = time.monotonic

_states = {}
_transitions = []
_probe_results = []


def _check_site(site):
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串: %r" % (site,))


def _entry(site, proxy):
    key = (site, proxy)
    entry = _states.get(key)
    if entry is None:
        entry = {"state": CLOSED, "failures": 0, "opened_at": None}
        _states[key] = entry
    return entry


def _set_state(site, proxy, entry, new_state):
    old = entry["state"]
    if old == new_state:
        return
    entry["state"] = new_state
    _transitions.append((site, proxy, old, new_state, _now()))


def acquire(site):
    """返回该站点当前可用的出口；全部熔断或探测中则抛出 RuntimeError。"""
    _check_site(site)
    now = _now()
    for proxy in PROXIES:
        entry = _entry(site, proxy)
        state = entry["state"]
        if state == CLOSED:
            return proxy
        if state == OPEN and now - entry["opened_at"] >= COOLDOWN_SECONDS:
            _set_state(site, proxy, entry, HALF_OPEN)
            return proxy
    raise RuntimeError("站点 %r 所有出口均不可用（打开或半开探测中）" % (site,))


def report(site, proxy, ok):
    """上报一次请求结果，驱动状态迁移，返回该 (站点, 出口) 当前状态。"""
    _check_site(site)
    if proxy not in PROXIES:
        raise ValueError("未知出口: %r（须在 PROXIES 中）" % (proxy,))
    if not isinstance(ok, bool):
        raise TypeError("ok 必须是 bool: %r" % (ok,))
    entry = _entry(site, proxy)
    state = entry["state"]
    if state == HALF_OPEN:
        _probe_results.append((site, proxy, ok))
        if ok:
            entry["failures"] = 0
            entry["opened_at"] = None
            _set_state(site, proxy, entry, CLOSED)
        else:
            entry["opened_at"] = _now()
            _set_state(site, proxy, entry, OPEN)
    elif state == CLOSED:
        if ok:
            entry["failures"] = 0
        else:
            entry["failures"] += 1
            if entry["failures"] >= FAILURE_THRESHOLD:
                entry["opened_at"] = _now()
                _set_state(site, proxy, entry, OPEN)
    else:
        raise RuntimeError(
            "出口 %r 对站点 %r 处于打开态，不接受上报" % (proxy, site)
        )
    return entry["state"]


def state_of(site, proxy):
    """查询单个 (站点, 出口) 的当前状态。"""
    _check_site(site)
    if proxy not in PROXIES:
        raise ValueError("未知出口: %r（须在 PROXIES 中）" % (proxy,))
    return _entry(site, proxy)["state"]


def transitions():
    """状态迁移记录：(site, proxy, old, new, ts)。"""
    return list(_transitions)


def probe_results():
    """半开探针结果：(site, proxy, ok)。"""
    return list(_probe_results)


def state_counts():
    """各态 (站点, 出口) 数量。"""
    counts = {CLOSED: 0, OPEN: 0, HALF_OPEN: 0}
    for entry in _states.values():
        counts[entry["state"]] += 1
    return counts


def reset():
    """清空全部状态与记录（测试和演示用）。"""
    _states.clear()
    del _transitions[:]
    del _probe_results[:]
