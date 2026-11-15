"""机房切换灰度：先在灰度站点验证，确认新地址可用后再扩大范围。

流程：
1. begin_switch：只失效灰度站点并立即刷新，其它站点完全不动；
2. confirm_switch：校验灰度站点的新结果健康（新的一代、选中地址不在冷却中、
   健康度达标），通过后失效其余受影响站点完成全量切换；
3. 任一步刷新失败：保留旧地址并告警，调用方可 rollback_switch 回退。
机房恢复后只刷新受影响站点（invalidate），不做全站刷新。
"""

from dataclasses import dataclass


@dataclass
class Rollout:
    idc: str
    canary_hosts: list[str]
    remaining_hosts: list[str]
    generation_by_host: dict[str, int]
    stage: str = "canary"          # canary -> confirmed / rolled_back


class RolloutManager:
    def __init__(self, cache, min_health: float | None = None):
        self._cache = cache
        self._min_health = min_health if min_health is not None else 0.5
        self._rollouts: dict[str, Rollout] = {}

    def begin_switch(self, idc: str, canary_hosts: list[str]) -> dict:
        """灰度阶段：只刷新灰度站点，记录切换后的缓存代数用于确认。"""
        snap0 = self._cache.snapshot()
        for host in canary_hosts:
            if host not in snap0:
                # 灰度站点尚未缓存：先冷解析预热，保证后续能验证新地址
                self._cache.resolve(host)
        before = {
            host: snap0.get(host, {}).get("generation", 0)
            for host in canary_hosts
        }
        # 只失效灰度站点；非灰度站点不触碰
        refreshed = self._cache.invalidate(hosts=list(canary_hosts), sync=True)
        snap = self._cache.snapshot()
        generation_by_host = {
            host: snap.get(host, {}).get("generation", 0) for host in canary_hosts
        }
        affected = self._cache.affected_hosts(idc=idc)
        remaining = [h for h in affected if h not in canary_hosts]
        rollout = Rollout(
            idc=idc, canary_hosts=list(canary_hosts),
            remaining_hosts=remaining,
            generation_by_host=generation_by_host,
        )
        self._rollouts[idc] = rollout
        return {"idc": idc, "stage": rollout.stage,
                "canary_refreshed": refreshed,
                "remaining_hosts": remaining,
                "generations": generation_by_host,
                "before_generations": before}

    def confirm_switch(self, idc: str) -> dict:
        """确认灰度结果健康，再把切换扩大到其余受影响站点。"""
        rollout = self._require_rollout(idc)
        snap = self._cache.snapshot()
        checked = []
        for host in rollout.canary_hosts:
            entry = snap.get(host)
            if entry is None:
                self._alert(idc, host, "canary entry missing after refresh")
                return self._fail(rollout, host, "entry missing")
            # 必须是灰度刷新产生的新一代（或更新），且选中地址健康、不在冷却中
            if entry["generation"] < rollout.generation_by_host.get(host, 0):
                self._alert(idc, host, "canary still serves old generation")
                return self._fail(rollout, host, "old generation")
            top = self._top_address(entry)
            if top is None or top["cooling"] or top["health"] < self._min_health:
                self._alert(idc, host, f"canary address unhealthy: {top}")
                return self._fail(rollout, host, "unhealthy address")
            if not entry["last_refresh_ok"]:
                self._alert(idc, host, "canary refresh did not succeed")
                return self._fail(rollout, host, "refresh failed")
            checked.append(host)
        # 灰度验证通过：扩大到同一机房的其余受影响站点
        self._cache.invalidate(hosts=rollout.remaining_hosts, sync=True)
        rollout.stage = "confirmed"
        return {"idc": idc, "stage": "confirmed",
                "canary_checked": checked,
                "promoted_hosts": rollout.remaining_hosts}

    def rollback_switch(self, idc: str) -> dict:
        """回退：重新刷新灰度站点（后端地址回退后即恢复旧地址）。"""
        rollout = self._require_rollout(idc)
        self._cache.invalidate(hosts=rollout.canary_hosts, sync=True)
        rollout.stage = "rolled_back"
        return {"idc": idc, "stage": "rolled_back",
                "rerolled_hosts": rollout.canary_hosts}

    def switch_status(self, idc: str) -> dict:
        rollout = self._require_rollout(idc)
        return {"idc": idc, "stage": rollout.stage,
                "canary_hosts": rollout.canary_hosts,
                "remaining_hosts": rollout.remaining_hosts}

    @staticmethod
    def _top_address(entry: dict):
        addresses = entry.get("addresses", [])
        healthy = [a for a in addresses if not a["cooling"]]
        if not healthy:
            return None
        return sorted(healthy, key=lambda a: -a["health"])[0]

    def _require_rollout(self, idc: str) -> Rollout:
        if idc not in self._rollouts:
            raise KeyError(f"no switch in progress for idc {idc!r}")
        return self._rollouts[idc]

    def _alert(self, idc: str, host: str, reason: str) -> None:
        self._cache.alert({
            "type": "canary_switch_blocked", "idc": idc,
            "host": host, "reason": reason,
        })

    @staticmethod
    def _fail(rollout: Rollout, host: str, reason: str) -> dict:
        return {"idc": rollout.idc, "stage": rollout.stage,
                "confirmed": False, "failed_host": host, "reason": reason,
                "message": "canary verification failed, old addresses are kept"}
