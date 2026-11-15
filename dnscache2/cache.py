"""带 TTL 和健康度的解析缓存。

核心策略：
- 按站点缓存解析结果，TTL 内复用；过期后先返回旧地址（stale-while-revalidate），
  后台异步刷新，刷新完成后再切换，避免请求阻塞在解析上；
- 地址按健康度排序，连续失败降权并指数冷却，冷却中的地址不会被选中；
- 刷新失败保留旧结果并告警，不把坏结果切进去；
- 机房变化时只失效受影响的站点（invalidate），配合 rollout 做灰度切换。
"""

import threading
import time
from dataclasses import dataclass, field

from .alerter import Alerter
from .exceptions import AllAddressesCoolingDownError, ResolutionError
from .executor import ThreadRefreshExecutor
from .health import AddressState, HealthConfig, pick, rank_addresses
from .resolver import Record


@dataclass
class Entry:
    host: str
    addresses: dict[str, AddressState] = field(default_factory=dict)
    expires_at: float = 0.0
    refresh_after: float = 0.0       # 失败退避后的最早刷新时间
    generation: int = 0              # 每次成功刷新 +1，灰度确认用
    last_refresh_ok: bool = True
    last_error: str | None = None


class DnsCache:
    def __init__(self, resolver, ttl: float = 30.0,
                 health_config: HealthConfig | None = None,
                 executor=None, clock=time.monotonic, alerter: Alerter | None = None):
        self._resolver = resolver
        self._ttl = ttl
        self._health = health_config or HealthConfig()
        self._executor = executor if executor is not None else ThreadRefreshExecutor()
        self._clock = clock
        self._alerter = alerter or Alerter()
        self._entries: dict[str, Entry] = {}
        self._refreshing: set[str] = set()
        self._lock = threading.RLock()
        # 冷路径单飞的等待/通知：独立普通锁，避免与缓存 RLock 的重入计数耦合
        self._cold_lock = threading.Lock()
        self._cold_cond = threading.Condition(self._cold_lock)

    # ---------- 解析主流程 ----------

    def resolve(self, host: str, force_refresh: bool = False) -> dict:
        """返回选中地址、剩余有效时间、可用程度、本次是否刷新过等信息。"""
        # 快速路径：缓存命中时只走缓存锁，不触碰冷路径条件变量
        with self._lock:
            entry = self._entries.get(host)
            now = self._clock()
            if entry is not None and entry.addresses:
                if force_refresh:
                    refreshed = self._refresh_sync(host, entry)
                    return self._build_result(host, self._entries[host], now,
                                              refreshed=refreshed)
                if entry.expires_at > now:
                    return self._build_result(host, entry, now, refreshed=False)
                # 已过期：旧地址仍然可用，后台刷新，本次返回旧结果
                self._schedule_refresh(host, entry)
                return self._build_result(host, entry, now, refreshed=False,
                                          stale=True)
        return self._resolve_cold(host)

    def _resolve_cold(self, host: str) -> dict:
        """冷路径单飞：一个线程锁外解析，其余在条件变量上等通知后复用结果。"""
        with self._cold_cond:
            while True:
                # 双检：等待期间可能已被别的线程解析并落库
                with self._lock:
                    entry = self._entries.get(host)
                if entry is not None and entry.addresses:
                    return self._build_result(host, entry, self._clock(),
                                              refreshed=False)
                if host not in self._refreshing:
                    self._refreshing.add(host)
                    break
                self._cold_cond.wait(timeout=10.0)

        # 持有者在两把锁之外做阻塞解析，不挡其它站点、不产生锁重入
        failure = None
        result = None
        try:
            result = self._cold_resolve(host)
        except BaseException as exc:  # 必须通知，否则等待者干等到超时
            failure = exc
        with self._cold_cond:
            self._refreshing.discard(host)
            self._cold_cond.notify_all()
        if failure is not None:
            raise failure
        return result

    def _cold_resolve(self, host: str) -> dict:
        """冷路径实际解析（不持任何锁）。"""
        now = self._clock()
        try:
            records = self._fetch(host)
        except ResolutionError as exc:
            self._alerter.alert({
                "type": "cold_resolve_failed", "host": host, "error": str(exc),
            })
            raise
        entry = self._apply_records(host, records, now)
        with self._lock:
            self._entries[host] = entry
        return self._build_result(host, entry, now, refreshed=True)

    # ---------- 刷新 ----------

    def _schedule_refresh(self, host: str, entry: Entry) -> None:
        """过期后后台刷新；同一站点同一时刻只允许一个刷新任务（防击穿）。"""
        now = self._clock()
        if host in self._refreshing or now < entry.refresh_after:
            return
        self._refreshing.add(host)
        self._executor.submit(self._refresh_async, host)

    def _refresh_async(self, host: str) -> None:
        try:
            with self._lock:
                entry = self._entries.get(host)
            if entry is None:
                return
            try:
                self._do_refresh(host, entry)
            except ResolutionError as exc:
                # 后台刷新失败：保留旧结果并告警，安排短退避后允许重试
                self._alerter.alert({
                    "type": "refresh_failed", "host": host, "error": str(exc),
                    "kept": "stale",
                })
                now = self._clock()
                with self._lock:
                    entry.refresh_after = now + min(self._ttl, 5.0)
                    entry.last_refresh_ok = False
                    entry.last_error = str(exc)
        finally:
            with self._lock:
                self._refreshing.discard(host)

    def _refresh_sync(self, host: str, entry: Entry) -> bool:
        """同步刷新（强制刷新/灰度预热用）。失败时保留旧地址并告警。"""
        try:
            self._do_refresh(host, entry)
            return True
        except ResolutionError as exc:
            self._alerter.alert({
                "type": "refresh_failed", "host": host, "error": str(exc),
                "kept": "stale",
            })
            now = self._clock()
            with self._lock:
                entry.refresh_after = now + min(self._ttl, 5.0)
                entry.last_refresh_ok = False
                entry.last_error = str(exc)
            return False

    def _do_refresh(self, host: str, entry: Entry) -> None:
        """执行一次刷新并整体替换地址列表；异常由调用方处理。

        若提交刷新后 entry 已被别的路径替换（如强制刷新先完成），则放弃本次落地，
        避免用旧 entry 算出的结果覆盖新结果。
        """
        records = self._fetch(host)
        now = self._clock()
        with self._lock:
            current = self._entries.get(host)
            if current is not entry:
                return
            new_entry = self._apply_records(host, records, now, previous=entry)
            self._entries[host] = new_entry

    def _fetch(self, host: str) -> list[Record]:
        records = self._resolver.resolve(host)
        records = self._coerce(host, records)
        if not records:
            raise ResolutionError(f"resolver returned no records for {host!r}")
        return records

    @staticmethod
    def _coerce(host: str, records) -> list[Record]:
        if isinstance(records, Record):
            return [records]
        out = []
        for rec in records:
            if isinstance(rec, Record):
                out.append(rec)
            elif isinstance(rec, dict):
                out.append(Record(ip=rec["ip"], idc=rec.get("idc")))
            elif isinstance(rec, (tuple, list)):
                ip, idc = (rec + (None,))[:2]
                out.append(Record(ip=ip, idc=idc))
            else:
                raise ResolutionError(f"bad record for {host!r}: {rec!r}")
        return out

    def _apply_records(self, host: str, records: list[Record], now: float,
                       previous: Entry | None = None) -> Entry:
        """新结果落地：保留同 IP 的健康度，新 IP 从满分开始，整体切换。"""
        old = previous.addresses if previous else {}
        addresses: dict[str, AddressState] = {}
        for rec in records:
            if rec.ip in old:
                state = old[rec.ip]
                state.idc = rec.idc
            else:
                state = AddressState(ip=rec.ip, idc=rec.idc)
            addresses[rec.ip] = state
        return Entry(
            host=host,
            addresses=addresses,
            expires_at=now + self._ttl,
            refresh_after=0.0,
            generation=(previous.generation if previous else 0) + 1,
            last_refresh_ok=True,
            last_error=None,
        )

    # ---------- 健康度上报 ----------

    def report(self, host: str, ip: str, ok: bool, rtt: float) -> dict:
        """采集请求结果反馈：连续失败降权冷却，成功后缓慢恢复。"""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(host)
            state = entry.addresses.get(ip) if entry else None
            if state is None:
                # 地址已被刷新替换等正常情况：不打告警，避免刷屏淹没真实故障
                return {"host": host, "ip": ip, "ok": ok, "health": 1.0,
                        "refreshed": False, "cooling": False, "known": False}
            state.apply_report(ok, rtt, now, self._health)
            return {"host": host, "ip": ip, "ok": ok,
                    "health": round(state.score, 4),
                    "refreshed": False, "known": True,
                    "cooling": state.is_cooling(now)}

    # ---------- 定向失效（机房变化） ----------

    def invalidate(self, hosts: list[str] | None = None,
                   idc: str | None = None, sync: bool = False) -> list[str]:
        """只失效受影响的站点：按站点名或机房名匹配，并立即安排刷新。"""
        with self._lock:
            affected = self._affected_hosts(hosts, idc)
            now = self._clock()
            entries = []
            for host in affected:
                entry = self._entries.get(host)
                if entry is not None:
                    entry.expires_at = now
                    # 显式失效（机房切换/恢复）不受刷新失败退避限制
                    entry.refresh_after = 0.0
                    entries.append((host, entry))
            if not sync:
                for host, entry in entries:
                    self._schedule_refresh(host, entry)
                return affected
        for host, entry in entries:
            self._refresh_sync(host, entry)
        return affected

    def affected_hosts(self, hosts: list[str] | None = None,
                       idc: str | None = None) -> list[str]:
        """按站点名或机房名算出受影响站点（不修改缓存）。"""
        with self._lock:
            return self._affected_hosts(hosts, idc)

    def alert(self, event: dict) -> None:
        """对外暴露告警入口，供灰度等上层组件复用同一告警通道。"""
        self._alerter.alert(event)

    def _affected_hosts(self, hosts, idc) -> list[str]:
        if hosts is not None:
            return [h for h in hosts if h in self._entries]
        if idc is None:
            return list(self._entries)
        result = []
        for host, entry in self._entries.items():
            if any(state.idc == idc for state in entry.addresses.values()):
                result.append(host)
        return result

    # ---------- 结果组装 ----------

    def _build_result(self, host: str, entry: Entry, now: float,
                      refreshed: bool, stale: bool = False) -> dict:
        states = list(entry.addresses.values())
        chosen = pick(states, now)
        all_cooling = chosen is None
        if all_cooling:
            # 全部冷却：不选冷却地址，抛给上层，避免请求继续打坏机器
            raise AllAddressesCoolingDownError(
                f"all addresses of {host!r} are cooling down"
            )
        candidates = rank_addresses(states, now)
        return {
            "host": host,
            "ip": chosen.ip,
            "idc": chosen.idc,
            "ttl": max(0.0, entry.expires_at - now),   # 剩余有效时间（秒）
            "cached": not refreshed,
            "refreshed": refreshed,                      # 本次是否刷新过
            "stale": stale,                              # 过期但仍返回旧地址
            "health": round(chosen.score, 4),            # 选中地址的可用程度
            "cooling": False,
            "generation": entry.generation,
            "candidates": [
                {
                    "ip": s.ip, "idc": s.idc,
                    "health": round(s.score, 4),
                    "cooling": s.is_cooling(now),
                }
                for s in candidates
            ],
        }

    # ---------- 运维辅助 ----------

    def wait_for_refreshes(self, timeout: float | None = None) -> None:
        """等待已提交的后台刷新完成（ManualExecutor 则排空队列）。"""
        run_pending = getattr(self._executor, "run_pending", None)
        if run_pending is not None:
            while self._executor.pending():
                run_pending()
            return
        join = getattr(self._executor, "join", None)
        if join is not None:
            join(timeout)

    def snapshot(self) -> dict:
        """查看缓存内部状态，供监控/测试。"""
        now = self._clock()
        with self._lock:
            out = {}
            for host, entry in self._entries.items():
                out[host] = {
                    "generation": entry.generation,
                    "ttl_remaining": max(0.0, entry.expires_at - now),
                    "last_refresh_ok": entry.last_refresh_ok,
                    "addresses": [
                        {"ip": s.ip, "idc": s.idc, "health": round(s.score, 4),
                         "consec_failures": s.consec_failures,
                         "cooling": s.is_cooling(now)}
                        for s in entry.addresses.values()
                    ],
                }
            return out

    def shutdown(self) -> None:
        shutdown = getattr(self._executor, "shutdown", None)
        if shutdown is not None:
            shutdown()
