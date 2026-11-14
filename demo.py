"""大租户 vs 小租户：权重轮转、保底配额、高优抢占续跑演示。"""

from taskfair import Scheduler


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


def drain(sched, clock, count):
    order = []
    for _ in range(count):
        clock.tick(1)
        task = sched.next()
        if task is None:
            break
        order.append(task["id"])
        sched.complete(task["id"])
    return order


def main():
    clock = ManualClock()

    # 场景一：大租户权重 3，小租户权重 1 且每轮保底 1 个出队名额。
    sched = Scheduler(clock=clock)
    sched.configure("big", weight=3, guarantee=0, priority=0)
    sched.configure("small", weight=1, guarantee=1, priority=0)
    for index in range(6):
        sched.submit("big", {"id": "big-%d" % index})
    for index in range(2):
        sched.submit("small", {"id": "small-%d" % index})
    print("权重轮转+保底下的出队顺序:", drain(sched, clock, 8))

    # 场景二：高优队列抢占低优在执行任务，进度保存后续跑。
    sched = Scheduler(clock=clock)
    sched.configure("low", weight=1, guarantee=0, priority=0)
    sched.configure("critical", weight=1, guarantee=1, priority=10)
    sched.submit("low", {"id": "report", "work": 10})
    clock.tick(1)
    running = sched.next()
    print("正在执行:", running["id"])

    sched.submit("critical", {"id": "hotfix"})
    clock.tick(1)
    saved = sched.preempt("report", progress={"finished_steps": 4})
    print("被抢占:", saved["id"], "保存进度:", saved["progress"])
    clock.tick(1)
    hotfix = sched.next()
    print("高优任务先出队:", hotfix["id"])
    sched.complete(hotfix["id"])

    clock.tick(1)
    resumed = sched.next()
    print("低优任务带进度续跑:", resumed["id"], resumed["progress"])
    sched.complete(resumed["id"])

    print("各队列统计:")
    for name, data in sched.stats().items():
        print(
            "  %s: 出队=%d 总等待=%.1f 平均等待=%.2f 最大等待=%.1f 抢占=%d"
            % (
                name,
                data["dequeued"],
                data["total_wait"],
                data["avg_wait"],
                data["max_wait"],
                data["preemptions"],
            )
        )


if __name__ == "__main__":
    main()

