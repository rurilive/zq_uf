"""按站点适配的请求头池：每站点 UA 列表 + 备用头，封禁识别后自动切换并冷却。"""

import time

_BAN_STATUSES = {403, 429}
_BAN_FEATURES = ("captcha", "access denied", "verify you are a human", "blocked")
_CONSECUTIVE_403_LIMIT = 3
_COOLDOWN_SECONDS = 300.0

_now = time.monotonic

_GLOBAL_CONFIG = {
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) crawler-a",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) crawler-b",
        "Mozilla/5.0 (X11; Linux x86_64) crawler-c",
    ],
    "accept_language": "zh-CN,zh;q=0.9",
    "accept_encoding": "gzip, deflate",
    "extra_headers": {"Accept": "text/html,application/xhtml+xml"},
}

_SITE_CONFIG = {
    "video": {
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) video-a",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) video-b",
        ],
        "accept_language": "en-US,en;q=0.8",
        "accept_encoding": "gzip, deflate, br",
    },
    "forum": {
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) forum-a",
            "Mozilla/5.0 (X11; Linux x86_64) forum-b",
        ],
        "accept_language": "zh-TW,zh;q=0.9",
        # accept_encoding 缺省，回退全局
    },
}

_stats = {}  # 状态码 -> 次数（全局，tests 锁定该结构）
_sites = {}  # site -> 运行状态


def _config(site):
    cfg = dict(_GLOBAL_CONFIG)
    cfg.update(_SITE_CONFIG.get(site, {}))
    return cfg


def _state(site):
    st = _sites.get(site)
    if st is None:
        st = _sites[site] = {
            "index": 0,
            "cooldowns": {},  # ua -> 解除冷却的时间戳
            "consecutive_403": 0,
            "switches": [],   # 切换记录：from/to/reason/at
            "bans": {},       # ua -> 封禁次数
        }
    return st


def headers(site):
    """返回站点当前请求头；UA 取站点池当前项，语言/编码按站点配置，缺省回退全局。"""
    cfg = _config(site)
    st = _state(site)
    result = {
        "User-Agent": cfg["user_agents"][st["index"] % len(cfg["user_agents"])],
        "Accept-Language": cfg["accept_language"],
        "Accept-Encoding": cfg["accept_encoding"],
    }
    result.update(cfg.get("extra_headers", {}))
    return result


def report(site, status, response=None):
    """上报状态码（可选响应文本），识别封禁后自动切换；返回全局状态码统计。"""
    _stats[status] = _stats.get(status, 0) + 1
    st = _state(site)
    if 200 <= status < 400:
        st["consecutive_403"] = 0
    reason = _ban_reason(site, status, response)
    if reason:
        _switch(site, reason)
    return dict(_stats)


def _ban_reason(site, status, response):
    """按状态码和响应特征识别封禁；单次 403 不算，避免误判。"""
    if status not in _BAN_STATUSES:
        return None
    text = ""
    if isinstance(response, str):
        text = response.lower()
    elif isinstance(response, dict):
        text = " ".join(str(v) for v in response.items()).lower()
    for feature in _BAN_FEATURES:
        if feature in text:
            return "状态码 %s + 响应特征 %r" % (status, feature)
    if status == 429:
        return "状态码 429 限流"
    st = _state(site)
    st["consecutive_403"] += 1
    if st["consecutive_403"] >= _CONSECUTIVE_403_LIMIT:
        return "连续 %d 次 403" % st["consecutive_403"]
    return None


def _switch(site, reason):
    """旧 UA 进入冷却，选下一个不在冷却的备用 UA；记录切换原因。"""
    cfg = _config(site)
    st = _state(site)
    agents = cfg["user_agents"]
    old = agents[st["index"] % len(agents)]
    now = _now()
    st["bans"][old] = st["bans"].get(old, 0) + 1
    st["cooldowns"][old] = now + _COOLDOWN_SECONDS
    st["consecutive_403"] = 0
    for step in range(1, len(agents) + 1):
        cand = agents[(st["index"] + step) % len(agents)]
        if st["cooldowns"].get(cand, 0.0) <= now:
            st["index"] = (st["index"] + step) % len(agents)
            st["switches"].append({"from": old, "to": cand, "reason": reason, "at": now})
            return cand
    st["switches"].append(
        {"from": old, "to": old, "reason": reason + "（无可用备用，维持当前 UA）", "at": now}
    )
    return old


def stats(site=None):
    """输出当前头、切换记录（含原因）和封禁统计；site 缺省为全部已见站点。"""
    names = [site] if site else sorted(_sites)
    now = _now()
    out = {}
    for name in names:
        st = _state(name)
        out[name] = {
            "headers": headers(name),
            "switches": list(st["switches"]),
            "bans": dict(st["bans"]),
            "cooldowns": {
                ua: round(until - now, 3)
                for ua, until in st["cooldowns"].items()
                if until > now
            },
        }
    return out
