"""多信号加权投票的封禁决策模块。

五类封禁信号按站点加权累计投票，票数超过熔断阈值才熔断；
熔断后进入观察态，按低频探针探测，连续成功才解除；
白名单带过期时间，期内信号不计票。
"""

import time

NORMAL = "normal"
OBSERVING = "observing"
BLOCKED = "blocked"

# 信号权重：验证码、403、空页面、跳转、限速
WEIGHTS = {
    "403": 3,
    "captcha": 2,
    "ratelimit": 2,
    "empty": 1,
    "redirect": 1,
}
SUCCESS_SIGNALS = frozenset({"200", "ok"})

OBSERVE_THRESHOLD = 3   # 票数达到即进入观察态（停用流量）
FUSE_THRESHOLD = 5      # 票数达到即熔断
RECOVER_SUCCESSES = 2   # 连续成功探针数，达到才解除
PROBE_INTERVAL = 1.0    # 探针最小间隔（秒），保证低频


class _Site:
    def __init__(self):
        self.state = NORMAL
        self.votes = 0
        self.vote_detail = {}
        self.successes = 0
        self.last_probe = None


_blocked = {}      # site -> _Site，仅保留有余票或非正常的站点
_whitelist = {}    # site -> 过期时间戳
_transitions = []  # 状态迁移日志
_probes = []       # 探针结果日志


def _now(now):
    return time.time() if now is None else now


def _whitelisted(site, now):
    expire = _whitelist.get(site)
    if expire is None:
        return False
    if expire <= now:
        del _whitelist[site]
        return False
    return True


def _transition(site, record, new_state, now):
    if record.state == new_state:
        return
    _transitions.append({
        "site": site, "from": record.state, "to": new_state,
        "votes": record.votes, "at": now,
    })
    record.state = new_state


def observe(site, signal, now=None):
    """上报一个信号，返回站点当前是否应停用（观察态或熔断）。"""
    now = _now(now)
    if _whitelisted(site, now):
        return False
    record = _blocked.get(site)
    if signal in SUCCESS_SIGNALS:
        return _probe(site, record, now)
    weight = WEIGHTS.get(signal, 0)
    if weight == 0:
        return record is not None and record.state != NORMAL
    if record is None:
        record = _Site()
        _blocked[site] = record
    record.successes = 0
    record.votes += weight
    record.vote_detail[signal] = record.vote_detail.get(signal, 0) + 1
    if record.votes >= FUSE_THRESHOLD:
        _transition(site, record, BLOCKED, now)
    elif record.votes >= OBSERVE_THRESHOLD:
        _transition(site, record, OBSERVING, now)
    return record.state != NORMAL


def _probe(site, record, now):
    if record is None or record.state == NORMAL:
        return False
    if record.last_probe is not None and now - record.last_probe < PROBE_INTERVAL:
        _probes.append({"site": site, "at": now, "accepted": False,
                        "successes": record.successes})
        return True
    record.last_probe = now
    record.successes += 1
    _probes.append({"site": site, "at": now, "accepted": True,
                    "successes": record.successes})
    if record.state == BLOCKED:
        _transition(site, record, OBSERVING, now)
    if record.successes >= RECOVER_SUCCESSES:
        _transition(site, record, NORMAL, now)
        del _blocked[site]
        return False
    return True


def allow(site, now=None):
    """站点当前是否放行。白名单期内直接放行。"""
    now = _now(now)
    if _whitelisted(site, now):
        return True
    record = _blocked.get(site)
    return record is None or record.state == NORMAL


def whitelist(site, ttl, now=None):
    """人工加白，ttl 秒内信号不计票，返回过期时间戳。"""
    now = _now(now)
    _whitelist[site] = now + ttl
    _blocked.pop(site, None)
    return _whitelist[site]


def status(site, now=None):
    """站点票数与状态快照。"""
    now = _now(now)
    record = _blocked.get(site)
    return {
        "site": site,
        "state": NORMAL if record is None else record.state,
        "votes": 0 if record is None else record.votes,
        "vote_detail": {} if record is None else dict(record.vote_detail),
        "successes": 0 if record is None else record.successes,
        "whitelisted_until": _whitelist.get(site) if _whitelisted(site, now) else None,
    }


def transitions():
    """状态迁移日志。"""
    return list(_transitions)


def probes():
    """探针结果日志。"""
    return list(_probes)
