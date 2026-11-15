"""会话与出口粘性：有效期内固定出口，到期或失效才换绑，冷却后可回切。"""

import time

_POOL = ["proxy-a", "proxy-b", "proxy-c"]

TTL_SECONDS = 1800      # 绑定有效期
COOLDOWN_SECONDS = 300  # 换绑后旧出口的冷却时长
MAX_BINDINGS = 128      # 绑定数量上限，超限回收最久未用的
MAX_HISTORY = 1000      # 换绑记录保留条数

_bindings = {}   # (session, site) -> 绑定记录
_cooldowns = {}  # (session, site) -> {"proxy", "until"}
_failed = set()  # 当前失效的出口
_history = []    # 换绑记录，供导出回看
_rollout = None  # None 表示全量启用；否则仅集合内的会话启用粘性


def reset():
    """清空全部运行状态（测试用）。"""
    _bindings.clear()
    _cooldowns.clear()
    _failed.clear()
    _history.clear()
    global _rollout
    _rollout = None


def set_rollout(sessions):
    """灰度开关：传入会话集合则仅这些会话启用粘性，None 表示全量。"""
    global _rollout
    _rollout = None if sessions is None else set(sessions)


def mark_failed(proxy):
    """标记出口失效，后续绑定绕开它。"""
    _failed.add(proxy)


def mark_recovered(proxy):
    """标记出口恢复，冷却结束后可回切。"""
    _failed.discard(proxy)


def _now(now):
    return time.time() if now is None else now


def _enabled(session):
    return _rollout is None or session in _rollout


def _legacy(session, site, reason):
    """灰度未覆盖时的旧行为：取池里第一个，无有效期。"""
    return {"session": session, "site": site, "proxy": _POOL[0],
            "expires_in": 0, "rebound": False, "reason": reason,
            "previous": None, "cooldown_until": None,
            "hint": "灰度未覆盖，按原逻辑取第一个出口"}


def _pick(key, now):
    """选出口：冷却结束的旧出口优先回切；冷却中的旧出口和失效出口跳过。"""
    cd = _cooldowns.get(key)
    if cd and cd["until"] <= now and cd["proxy"] not in _failed:
        return cd["proxy"]  # 冷却结束，由本次换绑触发回切
    skip = set(_failed)
    if cd and cd["until"] > now:
        skip.add(cd["proxy"])
    for proxy in _POOL:
        if proxy not in skip:
            return proxy
    for proxy in _POOL:  # 全部在冷却时，退而求其次选未失效的
        if proxy not in _failed:
            return proxy
    return _POOL[0]


def _evict_if_needed():
    while len(_bindings) > MAX_BINDINGS:
        oldest = min(_bindings, key=lambda k: _bindings[k]["last_used"])
        del _bindings[oldest]


def _rebind(key, reason, now):
    old = _bindings.get(key)
    old_proxy = old["proxy"] if old else None
    proxy = _pick(key, now)
    rec = {"session": key[0], "site": key[1], "proxy": proxy,
           "bound_at": now, "expires_at": now + TTL_SECONDS,
           "last_used": now, "reason": reason, "previous": old_proxy}
    _bindings[key] = rec
    if old_proxy and old_proxy != proxy:
        _cooldowns[key] = {"proxy": old_proxy, "until": now + COOLDOWN_SECONDS}
    _history.append({"ts": now, "session": key[0], "site": key[1],
                     "from": old_proxy, "to": proxy, "reason": reason})
    if len(_history) > MAX_HISTORY:
        del _history[:-MAX_HISTORY]
    _evict_if_needed()
    return _view(rec, now, rebound=old is not None)


def _view(rec, now, rebound):
    cd = _cooldowns.get((rec["session"], rec["site"]))
    cooldown_until, hint = None, None
    if cd:
        if cd["until"] > now:
            cooldown_until = cd["until"]
            hint = "旧出口 %s 冷却中，%d 秒后可回切" % (
                cd["proxy"], int(cd["until"] - now))
        else:
            hint = "冷却已结束，下次换绑可回切 %s" % cd["proxy"]
    return {"session": rec["session"], "site": rec["site"],
            "proxy": rec["proxy"],
            "expires_in": max(0, int(rec["expires_at"] - now)),
            "rebound": rebound, "reason": rec["reason"],
            "previous": rec["previous"],
            "cooldown_until": cooldown_until, "hint": hint}


def bind(session, site, now=None):
    """取当前绑定：有效期内固定出口；到期或出口失效才换绑。"""
    now = _now(now)
    if not _enabled(session):
        return _legacy(session, site, "rollout-off")
    key = (session, site)
    rec = _bindings.get(key)
    if rec and rec["expires_at"] > now and rec["proxy"] not in _failed:
        rec["last_used"] = now
        return _view(rec, now, rebound=False)
    if rec is None:
        reason = "new"
    elif rec["proxy"] in _failed:
        reason = "proxy-failed"
    else:
        reason = "expired"
    return _rebind(key, reason, now)


def rebind(session, site, reason, now=None):
    """手动换绑：记录原因和旧出口，旧出口进入冷却。"""
    now = _now(now)
    if not _enabled(session):
        return _legacy(session, site, reason)
    return _rebind((session, site), reason, now)


def query(session, now=None):
    """按会话查询全部绑定（跨站点）。"""
    now = _now(now)
    return [_view(rec, now, rebound=False)
            for (s, _), rec in _bindings.items() if s == session]


def export():
    """导出当前绑定与换绑历史，供值班同学回看。"""
    return {"bindings": [dict(rec) for rec in _bindings.values()],
            "history": list(_history),
            "failed": sorted(_failed)}
