"""地址健康度：连续失败降权冷却，成功后缓慢恢复。

状态机：
  正常 -> 连续失败达到阈值后进入冷却，冷却期间不参与候选；
  冷却到期 -> 以低分半开重新进入候选；
  半开/正常成功 -> 分数按恢复步长慢慢回到 1.0；
  再次失败 -> 冷却时间按指数退避拉长。
"""

from dataclasses import dataclass


@dataclass
class HealthConfig:
    fail_threshold: int = 3          # 连续失败达到该值才进入冷却
    min_score: float = 0.05          # 降权下限，避免永久零权重
    score_decay: float = 0.5         # 每次失败后的分数乘子
    recovery_step: float = 0.1       # 每次成功恢复的分数增量
    cooldown_base: float = 1.0       # 冷却基数（秒）
    cooldown_cap: float = 60.0       # 冷却上限（秒）


@dataclass
class AddressState:
    ip: str
    idc: str | None = None
    score: float = 1.0
    rtt: float = 0.0
    consec_failures: int = 0
    cooldown_until: float = 0.0

    def is_cooling(self, now: float) -> bool:
        return self.cooldown_until > now

    def apply_report(self, ok: bool, rtt: float, now: float,
                     cfg: HealthConfig) -> "AddressState":
        """根据一次调用结果更新健康度，返回自身。"""
        self.rtt = rtt
        if ok:
            self.consec_failures = 0
            self.cooldown_until = 0.0
            # 冷却恢复后半开放行，分数慢慢回到候选，而不是立刻满血
            self.score = min(1.0, self.score + cfg.recovery_step)
        else:
            self.consec_failures += 1
            self.score = max(cfg.min_score, self.score * cfg.score_decay)
            if self.consec_failures >= cfg.fail_threshold:
                backoff = self.consec_failures - cfg.fail_threshold
                cooldown = min(cfg.cooldown_cap, cfg.cooldown_base * 2 ** backoff)
                self.cooldown_until = now + cooldown
        return self


def rank_addresses(states: list[AddressState], now: float) -> list[AddressState]:
    """按可用程度排序候选：健康度降序、RTT 升序、IP 升序保证确定性。"""
    return sorted(states, key=lambda s: (-s.score, s.rtt, s.ip))


def pick(states: list[AddressState], now: float):
    """选出当前可用地址；全部冷却时返回 None。"""
    usable = [s for s in states if not s.is_cooling(now)]
    if not usable:
        return None
    return rank_addresses(usable, now)[0]
