"""出口轮换：会话绑定 + 失败轮换 + 冷却回切，站点之间状态隔离。

纯标准库实现。选路按出口池顺序确定性选取，不使用随机，
同一状态下行为可复现，便于测试和排查。
"""
import time

PROXIES = ["10.0.0.1:80", "10.0.0.2:80", "10.0.0.3:80"]

# 切换原因
REASON_FAILURE = "failure_threshold"          # 失败达阈值，出口进冷却，被迫轮换
REASON_ALL_COOLDOWN = "all_proxies_cooldown"  # 全部出口冷却中，取最早解冻者兜底
REASON_SWITCHBACK = "cooldown_elapsed"        # 原出口冷却结束，会话回切


class ProxyRotator:
    """单站点的出口轮换器。

    参数:
        site: 站点标识，仅用于记录，不参与选路。
        proxies: 出口池，默认使用模块级 PROXIES。
        failure_threshold: 同一出口连续失败多少次后进入冷却并轮换。
        cooldown: 出口冷却时长（秒），冷却结束前不会被正常选中。
        session_ttl: 会话绑定有效期（秒），有效期内出口固定。
        clock: 时间函数，返回单调时间；测试可注入假时钟。
    """

    def __init__(
        self,
        site="default",
        proxies=None,
        failure_threshold=3,
        cooldown=60.0,
        session_ttl=300.0,
        clock=time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown < 0 or session_ttl < 0:
            raise ValueError("cooldown and session_ttl must be >= 0")
        self.site = site
        self._proxies = list(proxies) if proxies is not None else list(PROXIES)
        if not self._proxies:
            raise ValueError("proxies must not be empty")
        self._threshold = failure_threshold
        self._cooldown = float(cooldown)
        self._ttl = float(session_ttl)
        self._clock = clock
        # session -> {"proxy": 出口|None, "bound_at": 时间|None, "history": [历史出口]}
        self._sessions = {}
        # proxy -> {"failures": 连续失败数, "cooldown_until": 解冻时间|None}
        self._stats = {p: {"failures": 0, "cooldown_until": None} for p in self._proxies}
        # 统一事件流，rotation / switchback 都写这里
        self._events = []

    # ---------- 对外接口（签名保持不变） ----------

    def acquire(self, session):
        """取会话当前出口。

        有效期内且出口健康：返回绑定出口。否则重新选路：
        先看历史出口是否已冷却结束（回切），再选健康出口，
        全部冷却时取最早解冻者兜底。
        """
        now = self._clock()
        state = self._sessions.get(session)

        if state is not None and state["proxy"] is not None:
            if not self._is_cooling(state["proxy"], now) and \
                    now - state["bound_at"] <= self._ttl:
                return state["proxy"]
            # 绑定失效（到期或出口冷却）：当前出口入历史，等待重新选路
            state["history"].append(state["proxy"])
            old = state["proxy"]
            state["proxy"] = None
            state["bound_at"] = None
        else:
            old = state["history"][-1] if state and state["history"] else None

        # 1. 回切：历史出口冷却已结束，优先回到最近用过的一个
        for candidate in reversed(state["history"] if state else []):
            if candidate != old and not self._is_cooling(candidate, now):
                self._bind(session, candidate, now, state)
                self._log("switchback", session, old, candidate, now, REASON_SWITCHBACK)
                return candidate

        # 2. 正常选路：从健康出口里按池子顺序取
        available = [p for p in self._proxies if not self._is_cooling(p, now)]
        if available:
            if old in available:
                # 仅绑定到期、原出口仍健康：静默续期，不记切换
                pick = old
                reason = None
            else:
                pick = available[0]
                reason = REASON_FAILURE if old and self._is_cooling(old, now) else None
        else:
            # 3. 全部冷却：取冷却最早结束的出口兜底
            pick = min(
                self._proxies,
                key=lambda p: self._stats[p]["cooldown_until"] or now,
            )
            reason = REASON_ALL_COOLDOWN

        self._bind(session, pick, now, state)
        # 首次绑定（old 为 None）不是切换；仅在已有出口发生变化时记录
        if old is not None and pick != old:
            self._log("rotation", session, old, pick, now, reason)
        return pick

    def release(self, session, proxy, ok):
        """上报一次请求结果。

        ok=False 时累加该出口失败数，达到阈值则进入冷却；
        绑定该出口的会话立即解绑，下一次 acquire 触发轮换。
        ok=True 清空失败数并解除冷却。
        """
        now = self._clock()
        stats = self._stats.setdefault(
            proxy, {"failures": 0, "cooldown_until": None}
        )
        rotated = False
        reason = None
        cooldown_until = stats["cooldown_until"]

        if ok:
            stats["failures"] = 0
            stats["cooldown_until"] = None
            cooldown_until = None
        else:
            stats["failures"] += 1
            if stats["failures"] >= self._threshold:
                cooldown_until = now + self._cooldown
                stats["cooldown_until"] = cooldown_until
                stats["failures"] = 0
                rotated = True
                reason = REASON_FAILURE
                # 出口进冷却，解绑所有绑在它上面的会话
                for bound_session, state in self._sessions.items():
                    if state["proxy"] == proxy:
                        self._detach(bound_session, proxy)

        return {
            "session": session,
            "proxy": proxy,
            "ok": bool(ok),
            "rotated": rotated,
            "reason": reason,
            "failures": stats["failures"],
            "cooldown_until": cooldown_until,
        }

    # ---------- 状态与记录输出 ----------

    def proxies(self):
        """出口池（按选路顺序的副本）。"""
        return list(self._proxies)

    def bindings(self):
        """当前有效绑定：{session: proxy}。"""
        return {
            session: state["proxy"]
            for session, state in self._sessions.items()
            if state["proxy"] is not None
        }

    def cooldowns(self):
        """冷却中的出口及其解冻时间：{proxy: cooldown_until}。"""
        now = self._clock()
        return {
            proxy: stats["cooldown_until"]
            for proxy, stats in self._stats.items()
            if self._is_cooling(proxy, now)
        }

    def rotations(self):
        """轮换记录（失败轮换、兜底轮换），按时间先后。"""
        return [dict(e) for e in self._events if e["type"] == "rotation"]

    def switchbacks(self):
        """回切记录（冷却结束后回到历史出口），按时间先后。"""
        return [dict(e) for e in self._events if e["type"] == "switchback"]

    def last_switch_reason(self, session=None):
        """最近一次切换（轮换或回切）的原因；可按会话过滤，无记录返回 None。"""
        events = self._events if session is None else [
            e for e in self._events if e["session"] == session
        ]
        return events[-1]["reason"] if events else None

    # ---------- 内部实现 ----------

    def _bind(self, session, proxy, now, state):
        if state is None:
            state = {"proxy": None, "bound_at": None, "history": []}
            self._sessions[session] = state
        state["proxy"] = proxy
        state["bound_at"] = now

    def _detach(self, session, proxy):
        """出口进入冷却时，解绑绑在它上面的会话（保留历史以便回切）。"""
        state = self._sessions.get(session)
        if state is not None and state["proxy"] == proxy:
            state["history"].append(proxy)
            state["proxy"] = None
            state["bound_at"] = None

    def _is_cooling(self, proxy, now):
        until = self._stats.get(proxy, {}).get("cooldown_until")
        return until is not None and until > now

    def _log(self, event_type, session, old_proxy, new_proxy, now, reason):
        self._events.append({
            "type": event_type,
            "time": now,
            "site": self.site,
            "session": session,
            "old_proxy": old_proxy,
            "new_proxy": new_proxy,
            "reason": reason,
        })


# 站点级注册表：同站点复用同一实例，绑定/冷却/记录天然不跨站点共享。
_REGISTRY = {}


def get_rotator(site="default", **kwargs):
    """按站点获取轮换器实例；首次创建时 kwargs 生效，之后同站点忽略。"""
    if site not in _REGISTRY:
        _REGISTRY[site] = ProxyRotator(site=site, **kwargs)
    return _REGISTRY[site]


# 模块级默认实例，保持历史 acquire/release 调用方式可用。
_default = get_rotator("default")


def acquire(session):
    """默认站点的会话绑定取出口，签名同 ProxyRotator.acquire。"""
    return _default.acquire(session)


def release(session, proxy, ok):
    """默认站点的结果上报，签名同 ProxyRotator.release。"""
    return _default.release(session, proxy, ok)
