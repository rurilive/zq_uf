"""采集器的出口池：按站点会话粘滞，带摘除、冷却和站点隔离。"""
import random
import time

PROXIES = [
    "10.0.0.11:8080",
    "10.0.0.12:8080",
    "10.0.0.13:8080",
]

DEFAULT_STICKY_WINDOW = 300.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN = 60.0


class PoolError(Exception):
    """出口池的基础错误。"""


class PoolExhaustedError(PoolError):
    """某站点当前没有可分配的出口。"""


class _Binding:
    __slots__ = ("proxy", "expires_at", "reason")

    def __init__(self, proxy, expires_at, reason):
        self.proxy = proxy
        self.expires_at = expires_at
        self.reason = reason


class _ExitState:
    __slots__ = ("failures", "quarantined_until")

    def __init__(self):
        self.failures = 0
        self.quarantined_until = None


class StickyPool:
    """按 (site, session) 粘滞分配出口的池。

    - 同一会话在 sticky_window 秒内固定同一出口，过期或出口被摘除才重选；
    - 出口连续失败 failure_threshold 次被摘除，冷却 cooldown 秒后恢复；
    - 站点之间隔离：绑定与故障状态按站点独立，活跃绑定不互相占用。
    """

    def __init__(self, proxies, sticky_window=DEFAULT_STICKY_WINDOW,
                 failure_threshold=DEFAULT_FAILURE_THRESHOLD,
                 cooldown=DEFAULT_COOLDOWN, time_func=None, rng=None):
        proxies = list(proxies)
        if not proxies:
            raise PoolError("proxies 不能为空")
        if len(set(proxies)) != len(proxies):
            raise PoolError("proxies 存在重复项: %r" % (proxies,))
        if sticky_window < 0:
            raise ValueError("sticky_window 不能为负: %r" % (sticky_window,))
        if failure_threshold < 1:
            raise ValueError("failure_threshold 至少为 1: %r" % (failure_threshold,))
        if cooldown < 0:
            raise ValueError("cooldown 不能为负: %r" % (cooldown,))
        self.proxies = proxies
        self.sticky_window = float(sticky_window)
        self.failure_threshold = int(failure_threshold)
        self.cooldown = float(cooldown)
        self._now = time_func or time.monotonic
        self._rng = rng if rng is not None else random
        self._sites = {}
        self._events = []

    def acquire(self, site, session):
        """取站点 site 下会话 session 的出口，返回代理地址字符串。"""
        if not site or not session:
            raise ValueError("site 和 session 不能为空: %r %r" % (site, session))
        now = self._now()
        data = self._site(site)
        binding = data["bindings"].get(session)
        if binding is not None:
            if binding.expires_at <= now:
                reason = "expired"
            elif self._is_quarantined(data, binding.proxy, now):
                reason = "exit-quarantined"
            else:
                return binding.proxy
        else:
            reason = data["pending"].pop(session, "new-session")
        proxy = self._select(site, data, now)
        data["bindings"][session] = _Binding(proxy, now + self.sticky_window, reason)
        self._events.append({
            "site": site, "session": session, "proxy": proxy,
            "reason": reason, "at": now,
        })
        return proxy

    def report_failure(self, site, session, proxy):
        """上报出口失败，连续失败达阈值则摘除并冷却，返回本次是否触发摘除。"""
        if not site or not session:
            raise ValueError("site 和 session 不能为空: %r %r" % (site, session))
        if proxy not in self.proxies:
            raise PoolError("未知出口: %r" % (proxy,))
        now = self._now()
        data = self._site(site)
        binding = data["bindings"].get(session)
        if binding is not None and binding.proxy != proxy:
            raise PoolError(
                "会话 %r 绑定的是 %r，不能上报 %r 的失败"
                % (session, binding.proxy, proxy))
        state = data["exits"][proxy]
        state.failures += 1
        if state.failures < self.failure_threshold:
            return False
        state.failures = 0
        state.quarantined_until = now + self.cooldown
        if binding is not None:
            del data["bindings"][session]
            data["pending"][session] = "exit-quarantined"
        return True

    def report_success(self, site, session, proxy):
        """上报出口成功，清零该站点下该出口的连续失败计数。"""
        if not site or not session:
            raise ValueError("site 和 session 不能为空: %r %r" % (site, session))
        if proxy not in self.proxies:
            raise PoolError("未知出口: %r" % (proxy,))
        self._site(site)["exits"][proxy].failures = 0

    def bindings(self):
        """当前活跃绑定：{site: {session: {proxy, expires_in, reason}}}。"""
        now = self._now()
        result = {}
        for site, data in self._sites.items():
            active = {}
            for session, binding in list(data["bindings"].items()):
                if binding.expires_at <= now:
                    del data["bindings"][session]
                    continue
                active[session] = {
                    "proxy": binding.proxy,
                    "expires_in": round(binding.expires_at - now, 6),
                    "reason": binding.reason,
                }
            if active:
                result[site] = active
        return result

    def switch_log(self):
        """历次（重）选择记录，含切换原因。"""
        return list(self._events)

    def status(self):
        """池状态快照：参数 + 各站点可用/冷却中/已绑定的出口。"""
        now = self._now()
        sites = {}
        for site, data in self._sites.items():
            quarantined = {}
            for proxy, state in data["exits"].items():
                if self._is_quarantined(data, proxy, now):
                    quarantined[proxy] = round(state.quarantined_until - now, 6)
            bound = {}
            for session, binding in list(data["bindings"].items()):
                if binding.expires_at <= now:
                    del data["bindings"][session]
                    continue
                bound[session] = binding.proxy
            sites[site] = {
                "available": [p for p in self.proxies if p not in quarantined],
                "quarantined": quarantined,
                "bound": bound,
            }
        return {
            "sticky_window": self.sticky_window,
            "failure_threshold": self.failure_threshold,
            "cooldown": self.cooldown,
            "sites": sites,
        }

    def _site(self, site):
        if site not in self._sites:
            self._sites[site] = {
                "bindings": {},
                "pending": {},
                "exits": {proxy: _ExitState() for proxy in self.proxies},
            }
        return self._sites[site]

    def _is_quarantined(self, data, proxy, now):
        state = data["exits"][proxy]
        if state.quarantined_until is None:
            return False
        if state.quarantined_until > now:
            return True
        state.quarantined_until = None
        state.failures = 0
        return False

    def _occupied_elsewhere(self, site, proxy, now):
        for other_site, data in self._sites.items():
            if other_site == site:
                continue
            for binding in list(data["bindings"].values()):
                if binding.expires_at > now and binding.proxy == proxy:
                    return True
        return False

    def _select(self, site, data, now):
        candidates = [
            proxy for proxy in self.proxies
            if not self._is_quarantined(data, proxy, now)
            and not self._occupied_elsewhere(site, proxy, now)
        ]
        if not candidates:
            raise PoolExhaustedError(
                "站点 %r 暂无可用出口：%d 个出口全部被摘除冷却或被其他站点占用"
                % (site, len(self.proxies)))
        return self._rng.choice(candidates)


_default_pool = StickyPool(PROXIES, sticky_window=0.0)


def acquire(site, session):
    """兼容入口：共享默认池，sticky_window=0 不粘滞；要粘性请自建 StickyPool。"""
    return _default_pool.acquire(site, session)
