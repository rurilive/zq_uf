"""采集出口 DNS 缓存：TTL 复用、健康度选路、后台刷新与机房定向切换。"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

log = logging.getLogger("dnscache2")

DEFAULT_TTL = 30.0          # 缓存有效期（秒）
COOLDOWN_BASE = 5.0         # 首次失败冷却（秒）
COOLDOWN_MAX = 300.0        # 冷却上限（秒）
REFRESH_RETRY_BASE = 5.0    # 刷新失败后的首次重试间隔（秒）
REFRESH_RETRY_MAX = 300.0   # 刷新失败重试间隔上限（秒）
DECAY_TAU = 60.0            # 健康分恢复时间常数（秒）
RTT_ALPHA = 0.5             # RTT 滑动平均系数
HEALTH_OK_STEP = 0.1        # 一次成功恢复的健康分
HEALTH_FAIL_FACTOR = 0.5    # 一次失败的健康分惩罚系数
HEALTH_MIN = 0.05           # 健康分下限
DEFAULT_DATACENTER = "default"

# 采集出口配置的权威地址表：host -> {机房: [地址]}；也兼容 host -> [地址]
DEFAULT_UPSTREAM = {
    "shop": {DEFAULT_DATACENTER: ["10.0.0.1"], "dc2": ["10.0.0.2"]},
}


@dataclass
class _Addr:
    """单个地址的健康状态。"""

    health: float = 1.0
    rtt: float | None = None
    fails: int = 0
    cooldown_until: float = 0.0
    updated: float = 0.0


@dataclass
class _Entry:
    """单个站点的缓存条目。"""

    datacenter: str
    addresses: dict[str, _Addr]
    expires: float
    generation: int = 0      # 每成功刷新一次加一，用于判断“本次是否刷新过”
    resolved: bool = False   # 是否成功解析过（report 建的占位条目为 False）
    retry_at: float = 0.0    # 刷新失败后的退避时间
    refresh_fails: int = 0


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="dnscache2-refresh", daemon=True).start()


def _clean(addresses: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for ip in addresses or ():
        ip = str(ip)
        if ip and ip not in seen:
            seen.add(ip)
            result.append(ip)
    return result


def _error_text(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


class DNSCache:
    """按站点缓存解析结果，并按地址健康度选路。

    刷新失败保留旧条目；时钟与后台执行器均可注入，便于测试。
    """

    def __init__(
        self,
        upstream: Mapping | Callable[[str, str], Iterable[str]] | None = None,
        ttl: float = DEFAULT_TTL,
        clock: Callable[[], float] = time.time,
        datacenter: str = DEFAULT_DATACENTER,
        canary_hosts: Iterable[str] = (),
        run_async: Callable[[Callable[[], None]], None] | None = None,
        alert: Callable[[dict], None] | None = None,
    ) -> None:
        self.upstream = DEFAULT_UPSTREAM if upstream is None else upstream
        self.ttl = float(ttl)
        self.clock = clock
        self.datacenter = datacenter
        self.canary_hosts = tuple(canary_hosts)
        self._run_async = run_async or _spawn
        self._alert_sink = alert
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._errors: dict[str, str] = {}
        self._pending: set[str] = set()
        self.alerts: list[dict] = []

    # ------------------------------------------------------------------ API

    def resolve(self, host: str, force: bool = False, datacenter: str | None = None) -> dict:
        """解析站点，返回选中的地址、剩余 TTL、健康分和刷新标记。

        命中缓存直接返回；过期时先返回旧地址并在后台刷新；机房变化时
        同步刷新该站点（旧地址在刷新失败时继续保留）。
        """
        requested = datacenter or self.datacenter
        now = self.clock()
        with self._lock:
            entry = self._entries.get(host)

        needs_sync = (
            force
            or entry is None
            or (entry.datacenter != requested and now >= entry.retry_at)
            # 从未成功解析过：没有旧地址可服务，只能同步解析；
            # 失败退避期内不再重复打上游，避免空站点打爆解析
            or (not entry.resolved and now >= entry.retry_at)
        )
        if needs_sync:
            refreshed = self._refresh_sync(host, requested)
            now = self.clock()
            with self._lock:
                entry = self._entries.get(host)
                result = self._result(host, requested, entry, now)
            result["refreshed"] = refreshed
            result["cached"] = (not refreshed and entry is not None
                                and entry.resolved)
            return result

        with self._lock:
            entry = self._entries.get(host)
            if entry is None:  # 并发 clear 的兜底
                return self._result(host, requested, None, now)
            result = self._result(host, requested, entry, now)
            expired = entry.expires <= now

        if expired:
            result["refreshing"] = self._schedule_refresh(host, requested)
        return result

    def report(self, host: str, ip: str, ok: bool, rtt: float) -> dict:
        """上报一次访问结果，更新健康分、延迟和冷却。"""
        now = self.clock()
        with self._lock:
            entry = self._entries.get(host)
            if entry is None:
                # 未知站点：先记录健康度，过期时间为 0，下次 resolve 仍会刷新
                entry = _Entry(datacenter=self.datacenter, addresses={}, expires=0.0)
                self._entries[host] = entry
            addr = entry.addresses.get(ip)
            if addr is None:
                addr = _Addr(updated=now)
                entry.addresses[ip] = addr
            self._decay(addr, now)
            if ok:
                addr.health = min(1.0, addr.health + HEALTH_OK_STEP)
                if rtt is not None:
                    addr.rtt = (rtt if addr.rtt is None
                                else RTT_ALPHA * rtt + (1 - RTT_ALPHA) * addr.rtt)
                addr.fails = 0
                addr.cooldown_until = 0.0
            else:
                addr.health = max(HEALTH_MIN, addr.health * HEALTH_FAIL_FACTOR)
                addr.fails += 1
                penalty = min(COOLDOWN_MAX,
                              COOLDOWN_BASE * 2 ** min(addr.fails - 1, 6))
                addr.cooldown_until = now + penalty
            return {
                "host": host, "ip": ip, "ok": ok,
                "health": round(addr.health, 4),
                "cooling": addr.cooldown_until > now,
                "cooldown": round(max(0.0, addr.cooldown_until - now), 3),
                "fails": addr.fails,
                "refreshed": False,
            }

    def rank(self, host: str) -> list[dict]:
        """按可用程度排序的地址列表（冷却中的排最后，但不代表可选）。"""
        now = self.clock()
        with self._lock:
            entry = self._entries.get(host)
            if entry is None:
                return []
            return self._rank(entry, now)

    def clear(self, datacenter: str | None = None) -> None:
        """清空缓存；指定机房时只清空属于该机房的条目。"""
        with self._lock:
            if datacenter is None:
                removed = list(self._entries)
                self._entries.clear()
            else:
                removed = [h for h, e in self._entries.items()
                           if e.datacenter == datacenter]
                for host in removed:
                    del self._entries[host]
            for host in removed:
                self._errors.pop(host, None)

    def switch_datacenter(
        self,
        datacenter: str,
        hosts: Iterable[str] | None = None,
        canary: Iterable[str] | None = None,
        probe: Callable[[str, str, str], bool] | None = None,
    ) -> dict:
        """切换机房：灰度站点验证通过后再扩大范围。

        只处理当前机房与目标不同的站点；灰度失败则整体不切换并保留旧结果。
        单个站点刷新失败时保留其旧地址，其余站点继续切换。
        probe(host, ip, datacenter) 返回 False 的地址会被剔除。
        """
        with self._lock:
            affected = sorted(h for h, e in self._entries.items()
                              if e.datacenter != datacenter)
        if hosts is not None:
            wanted = set(hosts)
            affected = [h for h in affected if h in wanted]

        if not affected:
            with self._lock:
                self.datacenter = datacenter
            return {"datacenter": datacenter, "target": datacenter,
                    "validated": True, "canary": [], "switched": [], "failed": {}}

        pool = list(canary) if canary is not None else list(self.canary_hosts)
        canary_hosts: list[str] = []
        for host in pool:
            if host in affected and host not in canary_hosts:
                canary_hosts.append(host)
        if not canary_hosts:
            canary_hosts = [affected[0]]

        # 灰度阶段：只取地址并验证，不提交，任一失败整体放弃
        prepared: dict[str, list[str]] = {}
        failed: dict[str, str] = {}
        for host in canary_hosts:
            try:
                prepared[host] = self._fetch(host, datacenter, probe)
            except Exception as exc:  # noqa: BLE001 - 上游异常都按刷新失败处理
                failed[host] = _error_text(exc)
                with self._lock:
                    self._errors[host] = failed[host]
        if failed:
            self._alert("机房切换灰度验证失败，保留旧地址",
                        target=datacenter, failed=failed)
            return {"datacenter": self.datacenter, "target": datacenter,
                    "validated": False, "canary": canary_hosts,
                    "switched": [], "failed": failed}

        for host in canary_hosts:
            self._commit(host, datacenter, prepared[host])
        with self._lock:
            self.datacenter = datacenter

        switched = list(canary_hosts)
        for host in affected:
            if host in canary_hosts:
                continue
            if not self._refresh_sync(host, datacenter, probe):
                with self._lock:
                    failed[host] = self._errors.get(host, "刷新失败")
            else:
                switched.append(host)
        return {"datacenter": datacenter, "target": datacenter,
                "validated": True, "canary": canary_hosts,
                "switched": switched, "failed": failed}

    # ------------------------------------------------------------- internals

    def _lookup(self, host: str, datacenter: str) -> list[str]:
        upstream = self.upstream
        if callable(upstream):
            return _clean(upstream(host, datacenter))
        record = upstream.get(host)
        if record is None:
            return []
        if isinstance(record, Mapping):
            # 明确按机房配置时不做回退：目标机房缺记录视为刷新失败，保留旧地址
            record = record.get(datacenter, [])
        return _clean(record)

    def _fetch(self, host: str, datacenter: str,
               probe: Callable[[str, str, str], bool] | None) -> list[str]:
        addresses = self._lookup(host, datacenter)
        if not addresses:
            raise LookupError(f"{host} 在机房 {datacenter} 无可用地址")
        if probe is not None:
            addresses = [ip for ip in addresses if probe(host, ip, datacenter)]
            if not addresses:
                raise LookupError(f"{host} 在机房 {datacenter} 的地址未通过探活")
        return addresses

    def _refresh_sync(self, host: str, datacenter: str,
                      probe: Callable[[str, str, str], bool] | None = None,
                      expect: tuple[int, str] | None = None) -> bool:
        """同步刷新；expect=(generation, datacenter) 用于丢弃过时的后台结果。"""
        try:
            addresses = self._fetch(host, datacenter, probe)
        except Exception as exc:  # noqa: BLE001 - 解析失败一律保留旧结果
            self._refresh_failed(host, datacenter, exc, expect)
            return False
        # 取地址在锁外进行，提交时再校验 generation，避免覆盖期间发生的新结果
        return self._commit(host, datacenter, addresses, expect)

    def _commit(self, host: str, datacenter: str, addresses: list[str],
                expect: tuple[int, str] | None = None) -> bool:
        """提交一次解析结果；expect 不匹配说明已过期，丢弃本次结果。"""
        now = self.clock()
        with self._lock:
            old = self._entries.get(host)
            if expect is not None and (
                old is None
                or old.generation != expect[0]
                or old.datacenter != expect[1]
            ):
                return False
            merged: dict[str, _Addr] = {}
            for ip in addresses:
                prior = old.addresses.get(ip) if old is not None else None
                merged[ip] = prior if prior is not None else _Addr(updated=now)
            generation = (old.generation if old is not None else 0) + 1
            self._entries[host] = _Entry(
                datacenter=datacenter,
                addresses=merged,
                expires=now + self.ttl,
                generation=generation,
                resolved=True,
            )
            self._errors.pop(host, None)
            return True

    def _refresh_failed(self, host: str, datacenter: str,
                        exc: BaseException,
                        expect: tuple[int, str] | None = None) -> None:
        now = self.clock()
        with self._lock:
            entry = self._entries.get(host)
            # 后台任务对应的条目已被清除或换过：本次失败作废，不要复活旧站点
            stale = expect is not None and (
                entry is None
                or entry.generation != expect[0]
                or entry.datacenter != expect[1])
            if not stale:
                self._errors[host] = _error_text(exc)
                if entry is None:
                    # 从未解析成功的站点也建占位条目，记录退避，避免反复打上游
                    entry = _Entry(datacenter=datacenter, addresses={},
                                   expires=0.0)
                    self._entries[host] = entry
                entry.refresh_fails += 1
                backoff = (REFRESH_RETRY_BASE
                           * 2 ** min(entry.refresh_fails - 1, 6))
                entry.retry_at = now + min(REFRESH_RETRY_MAX, backoff)
        if not stale:
            self._alert("解析刷新失败，保留旧地址",
                        host=host, datacenter=datacenter,
                        error=_error_text(exc))

    def _schedule_refresh(self, host: str, datacenter: str) -> bool:
        now = self.clock()
        with self._lock:
            entry = self._entries.get(host)
            if entry is None or host in self._pending or now < entry.retry_at:
                return False
            self._pending.add(host)
            expect = (entry.generation, entry.datacenter)

        def job() -> None:
            try:
                self._refresh_sync(host, datacenter, expect=expect)
            finally:
                with self._lock:
                    self._pending.discard(host)

        try:
            self._run_async(job)
        except Exception:  # noqa: BLE001 - 后台调度失败不应影响本次解析
            with self._lock:
                self._pending.discard(host)
            log.exception("后台刷新调度失败 host=%s", host)
            return False
        return True

    def _decay(self, addr: _Addr, now: float) -> None:
        """失败惩罚随时间指数衰减，健康分慢慢恢复到满分。"""
        elapsed = now - addr.updated
        if elapsed <= 0:
            return
        addr.health = 1.0 - (1.0 - addr.health) * math.exp(-elapsed / DECAY_TAU)
        addr.updated = now

    def _rank(self, entry: _Entry, now: float) -> list[dict]:
        ranked = []
        for ip, addr in entry.addresses.items():
            self._decay(addr, now)
            cooling = addr.cooldown_until > now
            ranked.append({
                "ip": ip,
                "health": round(addr.health, 4),
                "cooling": cooling,
                "rtt": round(addr.rtt, 4) if addr.rtt is not None else None,
                "_key": (cooling, -addr.health,
                         addr.rtt if addr.rtt is not None else math.inf, ip),
            })
        ranked.sort(key=lambda item: item["_key"])
        for item in ranked:
            del item["_key"]
        return ranked

    def _result(self, host: str, requested: str, entry: _Entry | None,
                now: float) -> dict:
        """在持锁状态下组装返回值。"""
        if entry is None:
            return {
                "host": host, "ip": None, "ttl": 0.0, "health": 0.0,
                "cached": False, "stale": False, "refreshed": False,
                "refreshing": False, "datacenter": None, "addresses": [],
                "reason": "unresolved", "error": self._errors.get(host),
            }

        ranked = self._rank(entry, now)
        chosen = next((item for item in ranked if not item["cooling"]), None)

        if chosen is None:
            if not ranked and not entry.resolved:
                reason = "unresolved"
            else:
                reason = "all_cooldown" if ranked else "no_address"
        elif entry.datacenter != requested:
            reason = "datacenter_stale"
        else:
            reason = None

        return {
            "host": host,
            "ip": chosen["ip"] if chosen else None,
            "ttl": round(max(0.0, entry.expires - now), 3),
            "health": chosen["health"] if chosen else 0.0,
            "cached": entry.resolved,
            "stale": entry.expires <= now,
            "refreshed": False,
            "refreshing": False,
            "datacenter": entry.datacenter,
            "addresses": ranked,
            "reason": reason,
            "error": self._errors.get(host),
        }

    def _alert(self, message: str, **fields) -> None:
        record = {"time": self.clock(), "message": message, **fields}
        with self._lock:
            self.alerts.append(record)
            if len(self.alerts) > 100:
                del self.alerts[:-100]
        try:
            if self._alert_sink is not None:
                self._alert_sink(record)
        except Exception:  # noqa: BLE001 - 告警回调异常不影响主流程
            log.exception("告警回调失败")
        log.warning("%s %s", message, fields or "")


_default = DNSCache()


def resolve(host: str, force: bool = False, datacenter: str | None = None) -> dict:
    """解析站点（使用进程内默认缓存）。"""
    return _default.resolve(host, force=force, datacenter=datacenter)


def report(host: str, ip: str, ok: bool, rtt: float) -> dict:
    """上报一次访问结果（使用进程内默认缓存）。"""
    return _default.report(host, ip, ok, rtt)


def rank(host: str) -> list[dict]:
    """返回站点地址的可用程度排序（使用进程内默认缓存）。"""
    return _default.rank(host)


def clear(datacenter: str | None = None) -> None:
    """清空默认缓存，可按机房定向清空。"""
    _default.clear(datacenter)


def switch_datacenter(datacenter: str, hosts: Iterable[str] | None = None,
                      canary: Iterable[str] | None = None,
                      probe: Callable[[str, str, str], bool] | None = None) -> dict:
    """切换默认缓存的机房（灰度验证后扩大范围）。"""
    return _default.switch_datacenter(datacenter, hosts=hosts,
                                      canary=canary, probe=probe)
