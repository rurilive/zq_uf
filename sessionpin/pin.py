"""会话出口粘性：带过期的会话绑定，站点隔离，按站点上限。

口径：
- 同一会话在同一站点、绑定有效期内固定出口；过期或出口失效才重绑。
- 重绑记录原因和旧出口；旧出口进入冷却，冷却结束后允许回切。
- 站点之间绑定互不影响；每站点绑定数有上限，超限拒绝新会话。
"""

import threading
import time

_DEFAULT_PROXIES = ("10.0.0.1:80", "10.0.0.2:80", "10.0.0.3:80")
_DEFAULT_TTL = 300.0         # 绑定有效期（秒）
_DEFAULT_COOLDOWN = 60.0     # 出口失效冷却（秒），冷却后可回切
_DEFAULT_MAX_BINDINGS = 128  # 每站点同时绑定上限


class BindingLimitError(Exception):
    """站点绑定数达到上限，拒绝新会话。"""


class NoAvailableExitError(Exception):
    """站点出口全部在冷却中，无可分配出口。"""


class _SiteConfig:
    def __init__(self):
        self.ttl = _DEFAULT_TTL
        self.cooldown = _DEFAULT_COOLDOWN
        self.max_bindings = _DEFAULT_MAX_BINDINGS
        self.proxies = _DEFAULT_PROXIES


_lock = threading.RLock()
_bindings = {}      # (session, site) -> binding
_configs = {}       # site -> _SiteConfig
_failed_until = {}  # proxy -> 冷却截止时刻（time.time()）
_records = []       # bind / rebind / switchback 记录
_rr = {}            # site -> 轮询游标


def _config(site):
    cfg = _configs.get(site)
    if cfg is None:
        cfg = _configs[site] = _SiteConfig()
    return cfg


def configure(site, ttl=None, cooldown=None, max_bindings=None, proxies=None):
    """按站点覆盖有效期、冷却、上限和出口池。"""
    with _lock:
        cfg = _config(site)
        if ttl is not None:
            cfg.ttl = float(ttl)
        if cooldown is not None:
            cfg.cooldown = float(cooldown)
        if max_bindings is not None:
            cfg.max_bindings = int(max_bindings)
        if proxies is not None:
            cfg.proxies = tuple(proxies)


def records():
    """返回绑定、重绑和回切记录的副本。"""
    with _lock:
        return [dict(r) for r in _records]


def _reset():
    """清空全部状态（测试和 demo 用）。"""
    with _lock:
        _bindings.clear()
        _configs.clear()
        _failed_until.clear()
        _records.clear()
        _rr.clear()


def _record(event, session, site, **kw):
    entry = {"event": event, "session": session, "site": site, "time": time.time()}
    entry.update(kw)
    _records.append(entry)


def _public(b):
    return {k: v for k, v in b.items() if k != "history"}


def _allocate(site, cfg, now):
    """选一个不在冷却中的出口；全部冷却返回 None。"""
    proxies = cfg.proxies
    start = _rr.get(site, 0)
    for i in range(len(proxies)):
        idx = (start + i) % len(proxies)
        proxy = proxies[idx]
        if _failed_until.get(proxy, 0.0) <= now:
            _rr[site] = idx + 1
            return proxy
    return None


def _site_count(site, now):
    return sum(
        1 for (s, st), b in _bindings.items()
        if st == site and b["expires"] > now
    )


def _prune(now):
    for key in [k for k, b in _bindings.items() if b["expires"] <= now]:
        del _bindings[key]


def _rebind_locked(b, cfg, reason, now, mark_failed=True):
    site = b["site"]
    old = b["proxy"]
    if mark_failed:
        _failed_until[old] = now + cfg.cooldown
    proxy = _allocate(site, cfg, now)
    if proxy is None:
        raise NoAvailableExitError("site %r: 出口全部冷却中" % site)
    event = "switchback" if proxy in b["history"] else "rebind"
    b["proxy"] = proxy
    b["created"] = now
    b["expires"] = now + cfg.ttl
    b["history"].append(proxy)
    _record(event, b["session"], site, reason=reason,
            old_proxy=old, new_proxy=proxy)
    return b


def bind(session, site):
    """绑定会话到站点出口；有效期内返回原绑定，过期或出口失效才重绑。"""
    now = time.time()
    with _lock:
        cfg = _config(site)
        key = (session, site)
        cur = _bindings.get(key)
        if cur is not None:
            if cur["expires"] > now and _failed_until.get(cur["proxy"], 0.0) <= now:
                return _public(cur)
            if cur["expires"] <= now:
                # 过期不算出口故障，不进入冷却
                return _public(_rebind_locked(cur, cfg, "expired", now,
                                              mark_failed=False))
            return _public(_rebind_locked(cur, cfg, "exit failed", now))
        _prune(now)
        if _site_count(site, now) >= cfg.max_bindings:
            raise BindingLimitError("site %r: 绑定数达到上限 %d"
                                    % (site, cfg.max_bindings))
        proxy = _allocate(site, cfg, now)
        if proxy is None:
            raise NoAvailableExitError("site %r: 出口全部冷却中" % site)
        b = {"session": session, "site": site, "proxy": proxy,
             "created": now, "expires": now + cfg.ttl, "history": [proxy]}
        _bindings[key] = b
        _record("bind", session, site, proxy=proxy, expires=b["expires"])
        return _public(b)


def rebind(session, reason):
    """因出口失效重绑会话的全部站点绑定，记录原因和旧出口。"""
    now = time.time()
    with _lock:
        result = {}
        for (s, site), b in list(_bindings.items()):
            if s == session:
                result = _rebind_locked(b, _config(site), reason, now)
        return _public(result) if result else {}
