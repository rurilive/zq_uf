"""出口健康管理：多次并发探测、打分、摘除与试探恢复。

只依赖 Python 标准库。带状态的健康管理由 :class:`HealthMonitor` 承担；
模块级 :func:`check` / :func:`pick` 维持历史的无状态接口。
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import socket
import threading
import time

__all__ = [
    "check",
    "pick",
    "HealthMonitor",
    "ProxyState",
    "HEALTHY",
    "EJECTED",
    "TRIALING",
    "UNKNOWN",
    "tcp_probe",
]

HEALTHY = "healthy"
EJECTED = "ejected"
TRIALING = "trialing"
UNKNOWN = "unknown"

_LATENCY_WINDOW = 20


class ProxyState:
    """单个出口的累计健康状态。"""

    def __init__(self, proxy):
        self.proxy = proxy
        self.status = UNKNOWN
        self.consecutive_failures = 0
        self.attempts = 0
        self.successes = 0
        self.total_attempts = 0
        self.total_successes = 0
        self.latency_samples = []
        self.last_error = None
        self.ejected_at = None

    @property
    def success_rate(self):
        """最近一轮探测的成功率，未探测过为 0。"""
        if self.attempts == 0:
            return 0.0
        return self.successes / self.attempts

    @property
    def avg_latency(self):
        if not self.latency_samples:
            return None
        return sum(self.latency_samples) / len(self.latency_samples)

    def snapshot(self):
        """可观测视图：状态、成功率、延迟与最近错误。"""
        return {
            "proxy": self.proxy,
            "status": self.status,
            "success_rate": round(self.success_rate, 4),
            "latency": round(self.avg_latency, 2) if self.avg_latency is not None else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "attempts": self.attempts,
            "successes": self.successes,
            "total_attempts": self.total_attempts,
            "total_successes": self.total_successes,
        }


def tcp_probe(timeout=2.0):
    """默认探针：对 ``host:port`` 出口做一次 TCP 连通探测，延迟单位毫秒。"""

    def probe(proxy):
        host, _, port = str(proxy).rpartition(":")
        start = time.monotonic()
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                pass
        except OSError as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
        return True, (time.monotonic() - start) * 1000.0, None

    return probe


class HealthMonitor:
    """带状态的出口健康管理器。

    参数:
        probe: 探测函数，入参出口，返回 ``(ok, latency)`` 或
            ``(ok, latency, error)``；抛异常按失败处理。
        attempts: 每个出口每轮连续探测次数。
        fail_threshold: 连续失败次数达到该阈值即摘除（按探测次数累计）。
        cooldown: 摘除后冷却秒数，冷却结束才放回试探。
        recovery_threshold: 试探轮成功率达到该比例才恢复为健康。
        workers: 并发探测线程数。
        round_timeout: 单轮巡检总耗时上限（秒），超时未完成的探测记失败。
        clock: 可注入的时钟函数，便于测试与演示冷却/恢复。
    """

    def __init__(
        self,
        probe=None,
        *,
        attempts=3,
        fail_threshold=3,
        cooldown=30.0,
        recovery_threshold=1.0,
        workers=8,
        round_timeout=10.0,
        clock=time.monotonic,
    ):
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self.probe = probe
        self.attempts = attempts
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self.recovery_threshold = recovery_threshold
        self.workers = max(1, workers)
        self.round_timeout = round_timeout
        self._clock = clock
        self._states = {}
        self._scores = {}
        self._lock = threading.RLock()

    def get_state(self, proxy):
        with self._lock:
            state = self._states.get(proxy)
            if state is None:
                state = ProxyState(proxy)
                self._states[proxy] = state
            return state

    def score(self, state):
        """按成功率(权重0.8)和延迟(权重0.2)打分，区间 [0, 1]。"""
        rate = state.success_rate
        latency = state.avg_latency or 0.0
        latency_score = 1.0 / (1.0 + latency / 100.0)
        return round(rate * 0.8 + latency_score * 0.2, 6)

    def check(self, pool, probe=None):
        """巡检一轮出口，返回与历史接口兼容的结果列表。

        每个元素仍是 ``{"proxy", "ok", "latency"}`` 三字段字典；
        更完整的状态/成功率/错误通过 :meth:`snapshot` 与 :meth:`report` 观察。
        """
        probe = probe or self.probe
        if probe is None:
            raise ValueError("a probe callable is required")

        pool = list(pool)
        now = self._clock()
        with self._lock:
            for proxy in pool:
                self._states.setdefault(proxy, ProxyState(proxy))

        trial_proxies = set()
        jobs = []
        for proxy in pool:
            state = self._states[proxy]
            if state.status == EJECTED:
                if state.ejected_at is not None and now - state.ejected_at < self.cooldown:
                    continue
                trial_proxies.add(proxy)
                state.status = TRIALING
            for index in range(self.attempts):
                jobs.append((proxy, index))

        outcomes = self._run_probes(jobs, probe, now + self.round_timeout)

        results = []
        with self._lock:
            for proxy in pool:
                state = self._states[proxy]
                samples = outcomes.get(proxy)
                if samples is not None:
                    self._update_state(state, samples, trial_proxies, now)
                    self._scores[proxy] = self.score(state)
                results.append(
                    {
                        "proxy": proxy,
                        "ok": state.status == HEALTHY,
                        "latency": state.avg_latency,
                    }
                )
        return results

    def pick(self, results):
        """从巡检结果中选得分最高的健康出口，全不可用返回 None。"""
        best_proxy = None
        best_score = None
        for item in results:
            if not item.get("ok"):
                continue
            proxy = item["proxy"]
            state = self._states.get(proxy)
            if state is not None and state.attempts:
                value = self._scores.get(proxy, self.score(state))
            else:
                latency = item.get("latency") or 0.0
                value = 0.8 + 0.2 / (1.0 + latency / 100.0)
            if best_score is None or value > best_score:
                best_proxy = proxy
                best_score = value
        return best_proxy

    def snapshot(self):
        """返回所有出口的可观测状态快照（按当前得分降序）。"""
        with self._lock:
            states = list(self._states.values())
        states.sort(key=lambda state: self._scores.get(state.proxy, self.score(state)), reverse=True)
        return [state.snapshot() for state in states]

    def report(self):
        """文本报告，逐出口展示状态、成功率、延迟与最近错误。"""
        lines = []
        for item in self.snapshot():
            latency = "-" if item["latency"] is None else f"{item['latency']:.1f}ms"
            error = item["last_error"] or "-"
            lines.append(
                f"{item['proxy']:<8} {item['status']:<9} "
                f"rate={item['success_rate']:.0%} latency={latency:<8} error={error}"
            )
        return "\n".join(lines)

    def _run_probes(self, jobs, probe, deadline):
        outcomes = {proxy: [] for proxy, _ in jobs}
        if not jobs:
            return outcomes
        executor = ThreadPoolExecutor(max_workers=min(self.workers, len(jobs)))
        try:
            futures = {
                executor.submit(self._probe_once, proxy, probe): (proxy, index)
                for proxy, index in jobs
            }
            timed_out = False
            for future, (proxy, index) in futures.items():
                if timed_out:
                    if future.done():
                        outcomes[proxy].append((index, future.result()))
                        continue
                    outcomes[proxy].append((index, (False, None, "timeout: round deadline exceeded")))
                    continue
                remaining = deadline - self._clock()
                try:
                    outcome = future.result(timeout=max(remaining, 1e-6))
                except FuturesTimeoutError:
                    timed_out = True
                    outcome = (False, None, "timeout: round deadline exceeded")
                outcomes[proxy].append((index, outcome))
        finally:
            executor.shutdown(wait=False)
        return outcomes

    def _probe_once(self, proxy, probe):
        try:
            outcome = probe(proxy)
        except Exception as exc:  # 探针自身异常视为一次失败
            return False, None, f"{type(exc).__name__}: {exc}"
        if isinstance(outcome, tuple):
            ok = bool(outcome[0])
            latency = outcome[1] if len(outcome) > 1 else None
            error = outcome[2] if len(outcome) > 2 else None
        else:
            ok, latency, error = bool(outcome), None, None
        if not ok and error is None:
            error = "probe returned failure"
        return ok, latency, error

    def _update_state(self, state, samples, trial_proxies, now):
        samples = [outcome for _, outcome in sorted(samples)]
        while len(samples) < self.attempts:
            samples.append((False, None, "timeout: round deadline exceeded"))

        successes = sum(1 for ok, _, _ in samples if ok)
        latencies = [latency for ok, latency, _ in samples if ok and latency is not None]
        last_error = next((error for _, _, error in samples if error), None)

        state.attempts = self.attempts
        state.successes = successes
        state.total_attempts += self.attempts
        state.total_successes += successes
        if latencies:
            state.latency_samples.extend(latencies)
            if len(state.latency_samples) > _LATENCY_WINDOW:
                del state.latency_samples[:-_LATENCY_WINDOW]

        if successes:
            state.consecutive_failures = 0
        state.consecutive_failures += self.attempts - successes
        state.last_error = last_error

        if state.proxy in trial_proxies:
            if state.success_rate >= self.recovery_threshold:
                state.status = HEALTHY
                state.ejected_at = None
                state.consecutive_failures = 0
            else:
                state.status = EJECTED
                state.ejected_at = now
        elif state.consecutive_failures >= self.fail_threshold:
            state.status = EJECTED
            state.ejected_at = now
        elif successes:
            state.status = HEALTHY


def check(pool, probe=None):
    """模块级巡检入口，接口保持 ``check(pool)`` / ``check(pool, probe)``。

    无状态：每次调用使用一次性巡检器，不做跨轮摘除/恢复。
    需要健康管理（摘除、冷却恢复、打分）时直接使用 :class:`HealthMonitor`。
    """
    return HealthMonitor(probe).check(pool)


def pick(site):
    """模块级选择入口，接口保持 ``pick(site)``。

    保留历史语义：返回第一个健康出口。需要按成功率/延迟打分排序时，
    请使用 :class:`HealthMonitor` 的 :meth:`HealthMonitor.pick`。
    """
    for item in site:
        if item.get("ok"):
            return item["proxy"]
    return None
