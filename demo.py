"""proxycheck 演示：稳定 / 抖动 / 全坏三类出口的健康管理。

用可手动推进的虚拟时钟演示多轮巡检、连续失败摘除、冷却试探恢复和打分选择，
不依赖真实网络与真实 sleep。
"""

from proxycheck import check, pick
from proxycheck.health import EJECTED, HEALTHY, HealthMonitor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# stable: 每次都快而稳；jittery: 约 1/3 概率失败；bad: 始终失败。
BEHAVIOR = {
    "stable": {"ok": True, "latency": 20.0, "error": None},
    "jittery": {"ok": True, "latency": 180.0, "error": None},
    "bad": {"ok": False, "latency": None, "error": "connection refused"},
}


def make_probe():
    calls = {"jittery": 0}

    def probe(proxy):
        spec = BEHAVIOR[proxy]
        if proxy == "jittery":
            calls["jittery"] += 1
            if calls["jittery"] % 3 == 0:
                return False, None, "transient timeout"
        return spec["ok"], spec["latency"], spec["error"]

    return probe


def main():
    pool = ["stable", "jittery", "bad"]
    clock = FakeClock()
    monitor = HealthMonitor(
        make_probe(),
        attempts=3,
        fail_threshold=3,
        cooldown=30.0,
        recovery_threshold=1.0,
        workers=8,
        round_timeout=5.0,
        clock=clock,
    )

    print("=== 第 1 轮：bad 连续失败 3 次被摘除，抖动出口仍留池 ===")
    results = monitor.check(pool)
    print(monitor.report())
    print("本轮选中:", monitor.pick(results))
    assert monitor.get_state("bad").status == EJECTED
    assert monitor.get_state("jittery").status == HEALTHY

    print("\n=== 冷却 10s：bad 仍在冷却，跳过探测 ===")
    clock.advance(10.0)
    monitor.check(pool)
    print(monitor.report())

    print("\n=== 冷却 30s 后：bad 放回试探，依旧失败则重新摘除 ===")
    clock.advance(30.0)
    monitor.check(pool)
    print(monitor.report())
    assert monitor.get_state("bad").status == EJECTED

    print("\n=== 让 bad 恢复：下一次试探全成功，重新计入可用 ===")
    BEHAVIOR["bad"] = {"ok": True, "latency": 60.0, "error": None}
    clock.advance(30.0)
    results = monitor.check(pool)
    print(monitor.report())
    assert monitor.get_state("bad").status == HEALTHY
    print("本轮选中:", monitor.pick(results), "（成功率相同时优先低延迟）")

    print("\n=== 模块级 check/pick 保持无状态历史接口 ===")
    simple = check(pool, make_probe())
    for item in simple:
        print(item["proxy"], item["ok"], item["latency"])
    print("第一个健康出口:", pick(simple))


if __name__ == "__main__":
    main()
