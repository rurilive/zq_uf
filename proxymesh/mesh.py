"""采集多机房出口选路：健康分 + 延迟 + 冷却 + 机房级故障切换。

对外接口保持 ``pick(site)`` / ``report(proxy, ok, rtt)``。旧的 ``health``
字段保留为 ``None``（兼容既有调用），实际健康分看 ``health_score``。
"""
import math
import time
from collections import deque

# 默认池：两个出口分属两个机房，便于在未注册时也能演示机房级切换。
_DEFAULT_POOL = (
    {"proxy": "proxy-a", "room": "room-a"},
    {"proxy": "proxy-b", "room": "room-b"},
)

_DEFAULTS = {
    # 延迟基准：rtt <= target 视为满分，超出按 target/rtt 折算
    "latency_target": 0.30,
    # 延迟在综合分中的调节幅度：score = health * (1 - w + w * latency_score)
    "latency_weight": 0.40,
    # 尚未测过延迟的出口给乐观分：先试一次拿到 rtt，避免恢复后拿不到流量
    "latency_unknown": 0.75,
    # 健康分更新
    "success_gain": 0.35,      # 成功：health += (1-health)*gain
    "failure_penalty": 0.50,   # 失败：health *= penalty
    "decay_tau": 60.0,         # 健康分向 1.0 衰减的时间常数（秒）
    "rtt_alpha": 0.30,         # 延迟 EWMA 系数
    # 连续失败与冷却
    "proxy_fail_threshold": 2,
    "room_fail_threshold": 3,
    "proxy_cooldown": 30.0,
    "room_cooldown": 60.0,
    "fail_window": 120.0,      # 超过该间隔的失败不累计连续计数
    # 选路日志条数
    "log_limit": 50,
}


def _infer_room(proxy):
    """未显式给机房时，用最后一个 '-' 之前的部分作为机房名。"""
    return proxy.rsplit("-", 1)[0] if "-" in proxy else proxy


class _ProxyState:
    __slots__ = (
        "proxy", "room", "health", "rtt", "last_update",
        "fail_streak", "last_failure", "cooldown_until",
    )

    def __init__(self, proxy, room, now):
        self.proxy = proxy
        self.room = room
        self.health = 1.0
        self.rtt = None
        self.last_update = now
        self.fail_streak = 0
        self.last_failure = 0.0
        self.cooldown_until = 0.0


class _RoomState:
    __slots__ = ("fail_streak", "last_failure", "cooldown_until")

    def __init__(self):
        self.fail_streak = 0
        self.last_failure = 0.0
        self.cooldown_until = 0.0


class Mesh:
    """选路与健康状态容器；模块级 ``pick``/``report`` 使用默认实例。"""

    def __init__(self, clock=None, **config):
        self._clock = clock or time.monotonic
        self.config = dict(_DEFAULTS)
        self.configure(**config)
        self._pools = {}          # site -> [proxy]
        self._site_proxies = {}   # proxy -> {site}
        self._proxies = {}        # proxy -> _ProxyState
        self._rooms = {}          # (site, room) -> _RoomState
        self._active = {}         # site -> 上次选中的 proxy
        self._last_site = {}      # proxy -> 最近一次被选中的 site
        self._locks = {}          # site -> 锁定的 room
        self._logs = {}           # site -> deque(选路记录)

    # ------------------------------------------------------------------ 配置
    def configure(self, clock=None, **overrides):
        if clock is not None:
            self._clock = clock
        for key, value in overrides.items():
            if key not in _DEFAULTS:
                raise ValueError("unknown config: %s" % key)
            self.config[key] = value

    def reset(self):
        """清空全部选路状态（保留 clock 与配置）。"""
        self._pools.clear()
        self._site_proxies.clear()
        self._proxies.clear()
        self._rooms.clear()
        self._active.clear()
        self._last_site.clear()
        self._locks.clear()
        self._logs.clear()

    # ------------------------------------------------------------------ 出口池
    def set_pool(self, site, proxies):
        """注册站点出口。

        ``proxies`` 元素可以是字符串（机房名由 ``_infer_room`` 推断），
        也可以是 ``{"proxy": ..., "room": ...}``。
        """
        entries = []
        for item in proxies:
            if isinstance(item, str):
                proxy, room = item, _infer_room(item)
            else:
                proxy = item["proxy"]
                room = item.get("room") or _infer_room(proxy)
            entries.append((proxy, room))
        if not entries:
            raise ValueError("pool for %r must not be empty" % (site,))

        old = self._pools.get(site, [])
        new_ids = [proxy for proxy, _ in entries]
        for proxy in old:
            if proxy in new_ids:
                continue
            owners = self._site_proxies.get(proxy)
            if owners is not None:
                owners.discard(site)
            if not owners:
                # 不再被任何站点引用，清掉健康状态，避免下次复用时带着旧分
                self._site_proxies.pop(proxy, None)
                self._proxies.pop(proxy, None)
                self._last_site.pop(proxy, None)

        self._pools[site] = new_ids
        for proxy, room in entries:
            state = self._proxies.get(proxy)
            now = self._clock()
            if state is None:
                state = _ProxyState(proxy, room, now)
                self._proxies[proxy] = state
            else:
                state.room = room
            self._site_proxies.setdefault(proxy, set()).add(site)
            self._rooms.setdefault((site, room), _RoomState())

        keep_rooms = {room for _, room in entries}
        for key in [k for k in self._rooms if k[0] == site and k[1] not in keep_rooms]:
            del self._rooms[key]
        if self._locks.get(site) not in keep_rooms:
            self._locks.pop(site, None)
        if self._active.get(site) not in new_ids:
            self._active.pop(site, None)

    def _ensure_pool(self, site):
        if site not in self._pools:
            self.set_pool(site, list(_DEFAULT_POOL))

    def lock_room(self, site, room):
        """手动锁定站点所在机房，自动切换不再跨出该机房。"""
        self._ensure_pool(site)
        rooms = {self._proxies[p].room for p in self._pools[site]}
        if room not in rooms:
            raise ValueError("unknown room %r for site %r" % (room, site))
        self._locks[site] = room

    def unlock_room(self, site):
        self._locks.pop(site, None)

    # ------------------------------------------------------------------ 打分
    def _decay(self, state, now):
        """健康分按时间向 1.0 回填；惩罚越久越淡。"""
        tau = self.config["decay_tau"]
        if now > state.last_update and tau > 0 and state.health < 1.0:
            gap = now - state.last_update
            state.health = 1.0 - (1.0 - state.health) * math.exp(-gap / tau)
        if now > state.last_update:
            state.last_update = now

    def _latency_score(self, rtt):
        if rtt is None:
            # 未知延迟给乐观分：选一次即测出 rtt，不靠随机探索
            return self.config["latency_unknown"]
        target = self.config["latency_target"]
        if target <= 0:
            return 1.0
        if rtt <= target:
            return 1.0
        return target / rtt

    def _score(self, health, rtt):
        """健康分作为乘性上限，延迟只在健康分之上做调节。

        ``score <= health`` 恒成立：延迟再好也只是逼近健康分本身，所以
        “快但坏”的出口不能靠延迟补回健康分；反过来，健康度明显更低的
        出口即使更快，也很难反超。
        """
        lw = self.config["latency_weight"]
        lw = min(1.0, max(0.0, lw))
        return health * (1.0 - lw + lw * self._latency_score(rtt))

    def _candidate(self, site, state, now, lock):
        self._decay(state, now)
        room_state = self._rooms.get((site, state.room)) or _RoomState()
        cooling = state.cooldown_until > now
        room_cooling = room_state.cooldown_until > now
        lock_conflict = lock is not None and state.room != lock
        eligible = not (cooling or room_cooling or lock_conflict)
        score = self._score(state.health, state.rtt) if eligible else None
        return {
            "proxy": state.proxy,
            "room": state.room,
            "score": score,
            "health_score": state.health,
            "rtt": state.rtt,
            "eligible": eligible,
            "cooling": cooling,
            "room_cooling": room_cooling,
            "lock_conflict": lock_conflict,
        }

    def _select(self, site, now):
        """返回一次选路结果（含全部候选、原因）；冷却出口不参与打分。"""
        self._ensure_pool(site)
        lock = self._locks.get(site)
        active = self._active.get(site)
        candidates = [
            self._candidate(site, self._proxies[p], now, lock)
            for p in self._pools[site]
        ]
        eligible = [c for c in candidates if c["eligible"]]

        if eligible:
            # 分数优先；同分时保持当前出口，再按名字稳定排序
            eligible.sort(
                key=lambda c: (
                    -c["score"],
                    0 if c["proxy"] == active else 1,
                    c["proxy"],
                )
            )
            best = eligible[0]
            excluded = [c["proxy"] for c in candidates if not c["eligible"]]
            suffix = ""
            if excluded:
                suffix = "；已排除冷却/锁定外出口 %s" % ",".join(excluded)
            if lock is not None:
                reason_code = "locked"
                reason = "机房手动锁定 %s，选综合分最高 %s (score=%.3f)%s" % (
                    lock, best["proxy"], best["score"], suffix)
            else:
                reason_code = "best"
                reason = "机房 %s 中健康分/延迟综合最优 %s (score=%.3f)%s" % (
                    best["room"], best["proxy"], best["score"], suffix)
        else:
            # 全部冷却：仅在锁定机房内或全局取“最不坏”的出口应急
            pool = candidates
            if lock is not None:
                locked = [c for c in candidates if c["room"] == lock]
                if locked:
                    pool = locked
            pool.sort(
                key=lambda c: (
                    -c["health_score"],
                    self._cooldown_left(c),
                    c["rtt"] if c["rtt"] is not None else float("inf"),
                    c["proxy"],
                )
            )
            best = pool[0]
            if lock is not None:
                reason_code = "locked_emergency"
                reason = "锁定机房 %s 内出口全部冷却，应急选健康分最高 %s" % (
                    lock, best["proxy"])
            else:
                reason_code = "emergency"
                reason = "全部出口冷却，应急选健康分最高 %s" % (best["proxy"],)

        return {
            "site": site,
            "proxy": best["proxy"],
            "room": best["room"],
            "health": None,  # 兼容字段，实际值看 health_score
            "health_score": best["health_score"],
            "score": best["score"],
            "rtt": best["rtt"],
            "candidates": candidates,
            "reason": reason,
            "reason_code": reason_code,
        }

    @staticmethod
    def _cooldown_left(candidate):
        # 排序用：冷却状态已消解的候选排前面
        return 0 if not (candidate["cooling"] or candidate["room_cooling"]) else 1

    # ------------------------------------------------------------------ 选路
    def pick(self, site):
        now = self._clock()
        selection = self._select(site, now)
        previous = self._active.get(site)
        switched = previous is not None and previous != selection["proxy"]
        self._active[site] = selection["proxy"]
        self._last_site[selection["proxy"]] = site
        self._record(site, selection, switched, selection["reason_code"],
                     selection["reason"])
        result = dict(selection)
        result["switched"] = switched
        return result

    def _affected_sites(self, proxy):
        return sorted(self._site_proxies.get(proxy, ()))

    def report(self, proxy, ok, rtt):
        now = self._clock()
        state = self._proxies.get(proxy)
        if state is None:
            state = _ProxyState(proxy, _infer_room(proxy), now)
            self._proxies[proxy] = state
        self._decay(state, now)

        cooled = False
        if ok:
            state.health = min(
                1.0, state.health + (1.0 - state.health) * self.config["success_gain"])
            state.fail_streak = 0
            state.last_failure = 0.0
            # 冷却只按时间到期解除：单次成功不让出口立刻重回打分
            if rtt is not None:
                alpha = self.config["rtt_alpha"]
                state.rtt = rtt if state.rtt is None else (
                    alpha * rtt + (1.0 - alpha) * state.rtt)
            reason_code = "success"
            reason = "%s 成功，健康分回升至 %.3f" % (proxy, state.health)
        else:
            state.health = max(0.0, state.health * self.config["failure_penalty"])
            state.fail_streak = self._bump_streak(
                state.fail_streak, state.last_failure, now)
            state.last_failure = now
            if (state.fail_streak >= self.config["proxy_fail_threshold"]
                    and state.cooldown_until <= now):
                state.cooldown_until = now + self.config["proxy_cooldown"]
                state.fail_streak = 0
                state.last_failure = 0.0
                cooled = True
            reason_code = "proxy_cooled" if cooled else "failure"
            reason = "%s 失败，健康分降至 %.3f%s" % (
                proxy, state.health, "，进入冷却" if cooled else "")

        sites = self._affected_sites(proxy)
        room_cooled = []
        for site in sites:
            room_state = self._rooms.get((site, state.room))
            if room_state is None:
                continue
            if ok:
                room_state.fail_streak = 0
                room_state.last_failure = 0.0
                continue
            room_state.fail_streak = self._bump_streak(
                room_state.fail_streak, room_state.last_failure, now)
            room_state.last_failure = now
            if room_state.cooldown_until > now:
                continue
            if (room_state.fail_streak >= self.config["room_fail_threshold"]
                    or self._all_room_cooling(site, state.room, now)):
                room_state.cooldown_until = now + self.config["room_cooldown"]
                room_state.fail_streak = 0
                room_state.last_failure = 0.0
                room_cooled.append(site)
                reason_code = "room_failed"
                reason = "机房 %s 连续故障，整体冷却并切走（%s 健康分 %.3f）" % (
                    state.room, proxy, state.health)

        switched = False
        selected = None
        # 只有失败才触发故障切换；单次失败仅降权，不切换
        if not ok:
            primary = self._last_site.get(proxy)
            ordered = ([primary] if primary in sites else []) + [
                s for s in sites if s != primary]
            for site in ordered:
                old = self._active.get(site)
                if old is None:
                    continue
                old_state = self._proxies.get(old)
                if old_state is None:
                    continue
                old_room = self._rooms.get((site, old_state.room))
                degraded = old_state.cooldown_until > now or (
                    old_room is not None and old_room.cooldown_until > now)
                if not degraded:
                    continue
                selection = self._select(site, now)
                if selection["proxy"] == old:
                    continue
                self._active[site] = selection["proxy"]
                self._last_site[selection["proxy"]] = site
                switched = True
                selected = selection
                if selection["room"] != old_state.room:
                    detail = "机房 %s -> %s" % (old_state.room, selection["room"])
                else:
                    detail = "同机房换出口 %s -> %s" % (
                        old, selection["proxy"])
                self._record(
                    site, selection, True, "failover",
                    "旧出口 %s 冷却/机房故障，%s" % (old, detail),
                )

        # 机房状态按该出口关联的所有站点汇总
        room_until = max(
            [self._rooms[(site, state.room)].cooldown_until
             for site in sites if (site, state.room) in self._rooms] or [0.0])
        return {
            "proxy": proxy,
            "ok": ok,
            "rtt": rtt,
            "health": None,
            "health_score": state.health,
            "rtt_ewma": state.rtt,
            "room": state.room,
            "cooling": state.cooldown_until > now,
            "cooldown_until": state.cooldown_until,
            "room_cooling": room_until > now,
            "room_cooldown_until": room_until,
            "switched": switched,
            "selected": selected["proxy"] if selected else None,
            "selection": selected,
            "reason": reason,
            "reason_code": reason_code,
        }

    def _bump_streak(self, streak, last_failure, now):
        """失败计数只在 ``fail_window`` 内累计，偶发失败不会攒成“连续”。"""
        window = self.config["fail_window"]
        if window <= 0 or now - last_failure > window:
            return 1
        return streak + 1

    def _all_room_cooling(self, site, room, now):
        proxies = [p for p in self._pools.get(site, ())
                   if self._proxies[p].room == room]
        return bool(proxies) and all(
            self._proxies[p].cooldown_until > now for p in proxies)

    # ------------------------------------------------------------------ 观测
    def _record(self, site, selection, switched, reason_code, reason):
        log = self._logs.get(site)
        if log is None:
            log = deque(maxlen=self.config["log_limit"])
            self._logs[site] = log
        log.append({
            "ts": self._clock(),
            "site": site,
            "proxy": selection["proxy"],
            "room": selection["room"],
            "score": selection["score"],
            "health_score": selection["health_score"],
            "switched": switched,
            "reason": reason,
            "reason_code": reason_code,
            "candidates": [dict(c) for c in selection["candidates"]],
        })

    def selection_log(self, site=None, limit=None):
        if site is None:
            entries = []
            for key in sorted(self._logs):
                entries.extend(self._logs[key])
        else:
            entries = list(self._logs.get(site, ()))
        if limit is not None:
            entries = entries[-limit:]
        return [dict(e, candidates=[dict(c) for c in e["candidates"]])
                for e in entries]

    def stats(self, site=None):
        if site is None:
            return {s: self._site_stats(s) for s in sorted(self._pools)}
        return self._site_stats(site)

    def _site_stats(self, site):
        if site not in self._pools:
            return {"site": site, "rooms": {}, "active": None,
                    "locked_room": None}
        now = self._clock()
        rooms = {}
        for proxy in self._pools[site]:
            state = self._proxies[proxy]
            self._decay(state, now)
            entry = rooms.setdefault(state.room, {
                "proxies": [],
                "cooling": 0,
                "health_scores": [],
                "rtts": [],
            })
            entry["proxies"].append(proxy)
            entry["health_scores"].append(state.health)
            if state.cooldown_until > now:
                entry["cooling"] += 1
            if state.rtt is not None:
                entry["rtts"].append(state.rtt)

        result = {}
        for room, entry in rooms.items():
            room_state = self._rooms.get((site, room)) or _RoomState()
            healths = entry["health_scores"]
            result[room] = {
                "proxies": list(entry["proxies"]),
                "cooling": entry["cooling"],
                "avg_health": sum(healths) / len(healths) if healths else 0.0,
                "best_rtt": min(entry["rtts"]) if entry["rtts"] else None,
                "cooling_room": room_state.cooldown_until > now,
                "fail_streak": room_state.fail_streak,
                "cooldown_until": room_state.cooldown_until,
            }
        return {
            "site": site,
            "rooms": result,
            "active": self._active.get(site),
            "locked_room": self._locks.get(site),
        }


_default = Mesh()


# ---------------------------------------------------------------------- 模块接口
def pick(site):
    """按健康分和延迟为站点选一个出口，返回候选、得分与原因。"""
    return _default.pick(site)


def report(proxy, ok, rtt):
    """记录一次抓取结果；失败会降权/冷却，触发故障时返回 switched。"""
    return _default.report(proxy, ok, rtt)


def set_pool(site, proxies):
    _default.set_pool(site, proxies)


def lock_room(site, room):
    _default.lock_room(site, room)


def unlock_room(site):
    _default.unlock_room(site)


def selection_log(site=None, limit=None):
    return _default.selection_log(site, limit)


def stats(site=None):
    return _default.stats(site)


def configure(clock=None, **overrides):
    _default.configure(clock=clock, **overrides)


def reset():
    _default.reset()
