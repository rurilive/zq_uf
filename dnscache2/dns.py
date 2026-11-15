"""带 TTL 与健康度的采集 DNS 缓存。

- 按站点缓存解析结果，TTL 内复用；过期后后台刷新，刷新期间旧地址继续可用，
  刷新完成后再切换到新地址（stale-while-revalidate）。
- 地址按健康度排序：连续失败降权并冷却，冷却中的地址不参与选择，恢复后逐步回到候选。
- 机房变更只定向刷新受影响站点，灰度站点验证通过后再扩大范围；刷新失败保留旧结果并告警。
"""

import threading
import time

# ---- 默认参数 -------------------------------------------------------------
DEFAULT_TTL = 30.0          # 解析结果有效时间（秒）
REFRESH_RETRY = 5.0         # 刷新失败后的重试间隔（秒），旧结果在此期间继续可用
FAIL_THRESHOLD = 3          # 连续失败达到该次数后进入冷却
HEALTH_PENALTY = 0.2        # 每次失败的健康度扣减
HEALTH_RECOVER = 0.1        # 每次成功的健康度恢复
COOLDOWN_BASE = 10.0        # 冷却基础时长（秒），按轮次指数退避
COOLDOWN_MAX = 300.0        # 冷却时长上限
GRAY_MIN_HEALTH = 0.5       # 灰度验证通过所需的最低健康度

_ADDR = {"shop": "10.0.0.1"}


class DnsError(Exception):
    """dnscache2 基础异常。"""


class DnsResolveError(DnsError):
    """首次加载且后端解析失败（无旧结果可保留）。"""


class NoAvailableAddress(DnsError):
    """站点所有地址都在冷却中，强制刷新后仍无可选地址。"""


class GrayVerificationError(DnsError):
    """灰度站点新地址未通过验证，拒绝扩大切换范围。"""


def _default_resolve(host):
    """系统解析的占位实现：固定表，未知主机回环。"""
    ip = _ADDR.get(host, "127.0.0.1")
    return [ip]


def _thread_submit(fn):
    """默认执行器：守护线程后台刷新，不阻塞调用方。"""
    threading.Thread(target=fn, name="dnscache2-refresh", daemon=True).start()


def _inline_submit(fn):
    """同步执行器，供测试与演示使用：刷新在当前调用内完成。"""
    fn()


class AddressState:
    """单个地址的健康状态。"""

    __slots__ = ("ip", "health", "failures", "cooldown_until",
                 "cooldown_round", "rtt", "last_ok_at")

    def __init__(self, ip):
        self.ip = ip
        self.health = 1.0           # 可用程度，1.0 为满分
        self.failures = 0           # 当前连续失败次数
        self.cooldown_until = 0.0   # 冷却截止时刻；未冷却为 0
        self.cooldown_round = 0     # 累计冷却轮次，决定退避时长
        self.rtt = None             # 最近一次上报的耗时
        self.last_ok_at = None

    def cooling(self, now):
        return now < self.cooldown_until


class _Entry:
    """单个站点的缓存条目。"""

    __slots__ = ("host", "addresses", "expire_at", "refreshing",
                 "refresh_done", "error", "generation")

    def __init__(self, host):
        self.host = host
        self.addresses = {}            # ip -> AddressState
        self.expire_at = 0.0           # TTL 截止时刻
        self.refreshing = False        # 后台刷新单飞标记
        self.refresh_done = False      # 一次性通知：后台刷新已完成
        self.error = None              # 最近一次刷新错误
        self.generation = 0            # 地址表版本，每次成功刷新 +1


class Rollout:
    """一次机房切换的灰度句柄。

    先刷新灰度站点，verify 通过后 expand 才刷新其余受影响站点；
    未受影响的站点全程不会被刷新。
    """

    def __init__(self, cache, affected, gray):
        self._cache = cache
        self.affected = list(affected)
        self.gray = list(gray)
        self.pending = [h for h in self.affected if h not in set(self.gray)]
        self.expanded = False

    def status(self):
        return {
            "gray": list(self.gray),
            "pending": list(self.pending),
            "expanded": self.expanded,
        }

    def verify(self):
        """确认灰度站点的新地址可用：刷新已完成、无错误、健康度达标。"""
        details = {}
        ok = True
        for host in self.gray:
            details[host] = self._cache._gray_healthy(host)
            ok = ok and details[host]
        return ok, details

    def expand(self):
        """灰度验证通过后，把切换扩大到其余受影响站点。"""
        ok, details = self.verify()
        if not ok:
            raise GrayVerificationError(details)
        self._cache.refresh_many(self.pending)
        self.pending = []
        self.expanded = True
        return details


class DnsCache:
    """按站点缓存、带 TTL 与健康度的解析缓存。"""

    def __init__(self, resolver=None, ttl=DEFAULT_TTL, *,
                 retry_interval=REFRESH_RETRY,
                 fail_threshold=FAIL_THRESHOLD,
                 health_penalty=HEALTH_PENALTY,
                 health_recover=HEALTH_RECOVER,
                 cooldown_base=COOLDOWN_BASE,
                 cooldown_max=COOLDOWN_MAX,
                 gray_min_health=GRAY_MIN_HEALTH,
                 time_func=time.monotonic,
                 submit=_thread_submit,
                 alerter=None):
        self._resolver = resolver or _default_resolve
        self._ttl = ttl
        self._retry_interval = retry_interval
        self._fail_threshold = fail_threshold
        self._health_penalty = health_penalty
        self._health_recover = health_recover
        self._cooldown_base = cooldown_base
        self._cooldown_max = cooldown_max
        self._gray_min_health = gray_min_health
        self._now = time_func
        self._submit = submit
        self._alerter = alerter
        self._entries = {}
        self._lock = threading.RLock()
        self.alerts = []   # 告警记录：{"at", "host", "reason", "detail"}

    # ---- 对外 API --------------------------------------------------------

    def resolve(self, host):
        """返回选中地址及缓存元信息。

        返回字段：host / ip（选中地址）/ ttl（剩余有效时间）/
        health（选中地址可用程度）/ cached（是否命中缓存）/
        refreshed（本次调用是否完成了一次刷新）。
        """
        now = self._now()
        with self._lock:
            entry = self._entries.get(host)
            first_load = entry is None
            if first_load:
                entry = _Entry(host)
                self._entries[host] = entry
                self._run_refresh(entry, now, allow_raise=True)
                refreshed = True
            else:
                if not entry.refreshing and now >= entry.expire_at:
                    self._trigger_refresh(entry)
                refreshed = self._consume_refresh_done(entry)

            addr = self._select(entry, now)
            if addr is None:
                # 全部地址冷却中：同步强制刷新一次争取新地址，仍无则抛出
                entry.refreshing = False
                refreshed = self._run_refresh(entry, now) or refreshed
                addr = self._select(entry, now)
                if addr is None:
                    raise NoAvailableAddress(host)

            return {
                "host": host,
                "ip": addr.ip,
                "ttl": max(0.0, entry.expire_at - now),
                "health": addr.health,
                "cached": not first_load,
                "refreshed": refreshed,
            }

    def report(self, host, ip, ok, rtt):
        """上报一次调用结果，驱动健康度降权、冷却与恢复。"""
        now = self._now()
        with self._lock:
            entry = self._entries.get(host)
            state = entry.addresses.get(ip) if entry is not None else None
            if state is None:
                # 未知站点/地址：忽略，保持旧调用方不报错
                return {"host": host, "ip": ip, "ok": ok,
                        "health": 1.0, "refreshed": False}

            state.rtt = rtt
            if ok:
                state.failures = 0
                state.last_ok_at = now
                if not state.cooling(now):
                    # 冷却结束后靠成功上报慢慢恢复权重
                    state.health = min(1.0, state.health + self._health_recover)
            else:
                state.failures += 1
                state.health = max(0.0, state.health - self._health_penalty)
                if state.failures >= self._fail_threshold:
                    state.cooldown_round += 1
                    cooldown = min(
                        self._cooldown_max,
                        self._cooldown_base * 2 ** (state.cooldown_round - 1),
                    )
                    state.cooldown_until = now + cooldown
                    state.failures = 0
                    self._alert(host, "address_cooldown",
                                {"ip": ip, "cooldown": cooldown,
                                 "round": state.cooldown_round})

            return {"host": host, "ip": ip, "ok": ok,
                    "health": state.health, "refreshed": False}

    def refresh(self, host):
        """强制刷新单个站点（后台进行，旧地址继续可用）。"""
        with self._lock:
            entry = self._entries.get(host)
            if entry is None:
                # 尚无缓存，没有旧结果可服务，同步加载
                entry = _Entry(host)
                self._entries[host] = entry
                self._run_refresh(entry, self._now(), allow_raise=True)
                return
            self._trigger_refresh(entry)

    def refresh_many(self, hosts):
        """定向刷新一批站点；不在列表中的站点不受影响。"""
        for host in dict.fromkeys(hosts):
            self.refresh(host)

    def begin_idc_switch(self, affected, gray):
        """机房切换：只刷新灰度站点，验证通过后再由 Rollout.expand 扩大。"""
        affected = list(dict.fromkeys(affected))
        gray = list(dict.fromkeys(gray))
        if not gray:
            raise ValueError("gray 站点不能为空")
        if not set(gray).issubset(set(affected)):
            raise ValueError("gray 必须是 affected 的子集")
        rollout = Rollout(self, affected, gray)
        self.refresh_many(gray)
        return rollout

    # ---- 内部实现 --------------------------------------------------------

    def _trigger_refresh(self, entry):
        """后台刷新，单飞：同一站点同时只允许一个刷新任务。"""
        if entry.refreshing:
            return
        entry.refreshing = True

        def job():
            with self._lock:
                try:
                    if self._run_refresh(entry, self._now()):
                        entry.refresh_done = True
                finally:
                    entry.refreshing = False

        self._submit(job)

    def _run_refresh(self, entry, now, allow_raise=False):
        """执行一次解析并合并结果。成功返回 True；失败保留旧结果并告警。"""
        try:
            ips = self._normalize(self._resolver(entry.host))
        except Exception as exc:  # 后端解析失败
            entry.error = str(exc)
            # 短间隔后再试；旧地址在此期间继续可用
            entry.expire_at = now + self._retry_interval
            self._alert(entry.host, "refresh_failed", {"error": str(exc)})
            if allow_raise:
                raise DnsResolveError(str(exc)) from exc
            return False

        # 刷新成功：整体替换地址表，未出现的旧地址即下线；
        # 仍存在的地址保留健康度/冷却状态，避免刷新抹掉故障记忆。
        merged = {}
        for ip in ips:
            merged[ip] = entry.addresses.get(ip) or AddressState(ip)
        entry.addresses = merged
        entry.expire_at = now + self._ttl
        entry.error = None
        entry.generation += 1
        return True

    @staticmethod
    def _normalize(raw):
        if isinstance(raw, str):
            ips = [raw]
        else:
            ips = list(raw)
        if not ips:
            raise ValueError("resolver 返回空地址列表")
        return ips

    def _select(self, entry, now):
        """冷却中的地址不参与选择，其余按健康度降序、RTT 升序排序。"""
        candidates = [a for a in entry.addresses.values()
                      if not a.cooling(now)]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (
            -a.health,
            a.rtt if a.rtt is not None else float("inf"),
            a.ip,
        ))
        return candidates[0]

    def _consume_refresh_done(self, entry):
        """后台刷新完成的一次性通知，下一次 resolve 体现为 refreshed=True。"""
        if entry.refresh_done:
            entry.refresh_done = False
            return True
        return False

    def _gray_healthy(self, host):
        """灰度验证：刷新已结束、无错误、选中地址健康度达标。"""
        entry = self._entries.get(host)
        if entry is None or entry.refreshing or entry.error:
            return False
        addr = self._select(entry, self._now())
        return addr is not None and addr.health >= self._gray_min_health

    def _alert(self, host, reason, detail):
        record = {"at": self._now(), "host": host,
                  "reason": reason, "detail": detail}
        self.alerts.append(record)
        if self._alerter is not None:
            self._alerter(record)


# 模块级默认单例，保持 resolve/report 的简单调用方式
_default = DnsCache()


def resolve(host):
    return _default.resolve(host)


def report(host, ip, ok, rtt):
    return _default.report(host, ip, ok, rtt)
