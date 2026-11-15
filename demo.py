"""演示：按任务分配预算、超时停止、降级留痕、断点续跑、影子观察。"""

import os
import tempfile
import time

from jobbudget3 import BudgetManager, policy


class Clock:
    """演示用的可控时钟。"""

    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


def main():
    workdir = tempfile.mkdtemp(prefix="jobbudget3-demo-")
    state_path = os.path.join(workdir, "state.json")
    clock = Clock()
    manager = BudgetManager(total_budget=12 * 3600, state_path=state_path, clock=clock)

    print("== 1. 按类型 + 历史耗时 + 优先级分配预算 ==")
    for spec in (
        {"name": "lint", "type": "quick", "priority": 4},
        {"name": "monthly-report", "type": "report", "priority": 5},
        {"name": "backfill", "type": "backfill", "priority": 9, "history": [4100, 4600, 5200]},
    ):
        result = manager.start(spec)
        print(
            f"{result['job']:<15} type={result['type']:<9} budget={result['budget']:>5.0f}s "
            f"(base={result['base_budget']}s, history={result['history_estimate']}, "
            f"x{result['priority_multiplier']:.2f})"
        )
        manager.finish(result["job"], used=result["budget"] * 0.6)

    print("\n== 2. 执行中按剩余比例降级，超时立即停止并保存断点 ==")
    job = {"name": "nightly-etl", "type": "etl", "priority": 5}
    started = manager.start(job)
    print(f"start: budget={started['budget']}s used={started['used']}s")
    for step, elapsed in ((1, 1800), (2, 1800), (3, 1800), (4, 1800)):
        clock.tick(elapsed)
        info = manager.checkpoint("nightly-etl", {"cursor": step * 1000})
        action = info["degradation"] or "-"
        print(
            f"checkpoint {step}: used={info['used']:.0f}s remaining={info['remaining']:.0f}s "
            f"degraded={info['degraded']} action={action} halted={info['halted']}"
        )
        if info["halted"]:
            print(f"  原因：{info['reason']}")
            print(f"  恢复位置：{info['resume_from']}")
            break

    print("\n== 3. 高优任务追加预算，超时停止后从断点继续 ==")
    urgent = {"name": "cust-migration", "type": "large", "priority": 9}
    started = manager.start(urgent, budget=600)
    print(f"start: budget={started['budget']:.0f}s (requested={started['requested_budget']:.0f}s)")
    clock.tick(400)
    status = manager.checkpoint("cust-migration", {"cursor": 2500})
    print(f"checkpoint 1: used={status['used']:.0f}s / budget={status['budget']:.0f}s")
    topup = manager.topup("cust-migration", 600, reason="客户事故单 INC-42 需当天完成")
    print(f"topup: granted={topup['granted']} budget={topup['budget']:.0f}s reason={topup['reason']}")

    clock.tick(900)
    stopped = manager.checkpoint("cust-migration", {"cursor": 2600})
    print(f"checkpoint 2: halted={stopped['halted']} status={stopped['status']} reason={stopped['reason']}")
    print(f"  恢复位置：{stopped['resume_from']}")
    resumed = manager.resume("cust-migration")
    print(
        f"resume: status={resumed['status']} budget={resumed['budget']:.0f}s "
        f"used={resumed['used']:.0f}s resume_from={resumed['resume_from']}"
    )

    print("\n== 4. 降级动作可查询 ==")
    for entry in manager.actions():
        print(f"  {entry['job']:<15} {entry['action']:<14} {entry['reason']}")

    print("\n== 5. 新预算影子运行一周，对比实际耗时 ==")
    shadow = BudgetManager(
        shadow=True,
        shadow_days=7,
        state_path=os.path.join(workdir, "shadow-state.json"),
        clock=clock,
    )
    shadow.start({"name": "probe", "type": "normal"}, budget=600)
    for elapsed in (300, 300, 120):
        clock.tick(elapsed)
        info = shadow.checkpoint("probe")
        print(f"  used={info['used']:.0f}s would={info['would']} halted={info['halted']}")
    shadow.finish("probe", used=720)
    report = shadow.shadow_report()
    print(
        f"  shadow jobs={report['jobs']} overruns={report['overruns']} "
        f"would_timeout={report['would_timeout']} false_kill_risk={report['false_kill_risk']}"
    )
    print(f"\n账本：{state_path}")


if __name__ == "__main__":
    main()
