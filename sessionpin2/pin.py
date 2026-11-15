"""会话与出口粘性：有效期内固定出口，失效重绑，冷却后可回切。"""

import time

_pool = ["proxy-a", "proxy-b"]

_config = {
    "ttl": 0.0,              # 绑定有效期秒数，0 表示不过期
    "cooldown": 0.0,         # 失效出口冷却秒数，冷却后可回切
    "max_bindings": None,    # 全局绑定数上限，None 不限
    "site_limits": {},       # 每站点绑定数上限，如 {"shop": 10}
}

# (session, site) -> 绑定记录；站点之间互相隔离
_bindings = {}
# (session, site) -> {proxy: 冷却截止时刻}
_cooldowns = {}
# 事件输出：bind / rebind / switchback
_events = []


def configure(ttl=None, cooldown=None, max_bindings=None, site_limits=None):
    """更新配置；传 None 的项保持不变。绑定池上限可按站点配置。"""
    if ttl is not None:
        _config["ttl"] = float(ttl)
    if cooldown is not None:
        _config["cooldown"] = float(cooldown)
    if max_bindings is not None:
        _config["max_bindings"] = int(max_bindings)
    if site_limits is not None:
        _config["site_limits"] = dict(site_limits)


def reset():
    """清空绑定、冷却与事件，并恢复默认配置。"""
    _bindings.clear()
    _cooldowns.clear()
    del _events[:]
    _config.update(ttl=0.0, cooldown=0.0, max_bindings=None, site_limits={})


def events():
    """按时间顺序返回绑定、重绑、回切事件。"""
    return list(_events)


def _expired(rec, now):
    exp = rec["expires_at"]
    return exp is not None and now >= exp


def _remaining(rec, now):
    if rec["expires_at"] is None:
        return 0
    return max(0, int(rec["expires_at"] - now))


def _available(key, now, exclude=()):
    down = _cooldowns.get(key, {})
    ready = [p for p in _pool if p not in exclude and down.get(p, 0.0) <= now]
    if not ready:
        ready = [p for p in _pool if p not in exclude]
    return ready or list(_pool)


def _evict_oldest(site=None):
    candidates = [(rec["last_used"], k) for k, rec in _bindings.items()
                  if site is None or k[1] == site]
    if candidates:
        del _bindings[min(candidates)[1]]


def _evict_if_needed(site):
    """绑定数超限时按最久未用回收，全局上限与站点上限都生效。"""
    limit = _config["max_bindings"]
    while limit is not None and len(_bindings) >= limit:
        _evict_oldest()
    site_limit = _config["site_limits"].get(site)
    while site_limit is not None and \
            sum(1 for k in _bindings if k[1] == site) >= site_limit:
        _evict_oldest(site)


def _create(session, site, now, exclude=(), reason=None, old_proxy=None):
    key = (session, site)
    _evict_if_needed(site)
    failed_before = _cooldowns.get(key, {})
    proxy = _available(key, now, exclude)[0]
    if proxy in failed_before and failed_before[proxy] <= now:
        # 回切：该出口此前失效过，冷却已结束，记录冷却时间
        _events.append({"event": "switchback", "session": session,
                        "site": site, "proxy": proxy,
                        "cooldown": _config["cooldown"], "at": now})
        del failed_before[proxy]
    ttl = _config["ttl"]
    rec = {"session": session, "site": site, "proxy": proxy,
           "bound_at": now, "expires_at": now + ttl if ttl else None,
           "last_used": now}
    _bindings[key] = rec
    _events.append({"event": "rebind" if old_proxy is not None else "bind",
                    "session": session, "site": site, "proxy": proxy,
                    "old_proxy": old_proxy, "reason": reason, "at": now})
    return rec


def _view(rec, now, rebound):
    return {"session": rec["session"], "site": rec["site"],
            "proxy": rec["proxy"], "expires_in": _remaining(rec, now),
            "rebound": rebound}


def bind(session, site):
    """同会话同站点有效期内固定出口；过期或不存在才新建绑定。"""
    now = time.time()
    rec = _bindings.get((session, site))
    if rec is not None and not _expired(rec, now):
        rec["last_used"] = now
        return _view(rec, now, False)
    return _view(_create(session, site, now), now, False)


def rebind(session, reason):
    """会话仍有效才允许重绑；记录原因和旧出口，旧出口进入冷却。"""
    now = time.time()
    keys = [k for k, rec in _bindings.items()
            if k[0] == session and not _expired(rec, now)]
    if not keys:
        raise ValueError("会话已失效或不存在，不能重绑: %r" % (session,))
    keys.sort(key=lambda k: _bindings[k]["last_used"])
    view = None
    for key in keys:
        old = _bindings.get(key)
        if old is None:  # 可能在本轮回收中被挤掉
            continue
        old_proxy = old["proxy"]
        if _config["cooldown"]:
            _cooldowns.setdefault(key, {})[old_proxy] = \
                now + _config["cooldown"]
        rec = _create(key[0], key[1], now, exclude=(old_proxy,),
                      reason=reason, old_proxy=old_proxy)
        view = {"session": rec["session"], "proxy": rec["proxy"],
                "reason": reason, "rebound": True,
                "cooldown": _config["cooldown"], "old_proxy": old_proxy}
    return view
