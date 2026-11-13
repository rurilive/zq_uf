"""公平队列演示：大小站点混合提交 + 站点配额。"""

from fairqueue import TaskQueue


class FakeClock:
    """手动推进的时钟，保证 demo 输出确定。"""

    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def main():
    print("第一段：大小站点混合提交（big 权重 2，small 权重 1）")
    clock = FakeClock()
    queue = TaskQueue(weights={"big": 2, "small": 1}, clock=clock)
    counts = {"big": 0, "small": 0}
    for _ in range(3):
        for site in ("big", "small", "big"):
            counts[site] += 1
            clock.now += 1
            queue.push({"id": "%s%d" % (site[0], counts[site]), "site": site})

    order = []
    while queue.size():
        clock.now += 1
        task = queue.next()
        if task is None:
            break
        order.append(task["id"])
    print("出队顺序:", " ".join(order))
    print("站点统计:")
    for site, stats in queue.stats().items():
        print(
            "  %s: 出队 %d, 平均等待 %.1f, 最大等待 %d"
            % (site, stats["dequeued"], stats["avg_wait"], stats["max_wait"])
        )

    print("第二段：站点配额 quota=1（慢站在途占满后不阻塞其他站点）")
    clock2 = FakeClock()
    queue2 = TaskQueue(quota=1, clock=clock2)
    pushed = {}
    for site, name in (("slow", "slow1"), ("slow", "slow2"), ("fast", "fast1"), ("fast", "fast2")):
        clock2.now += 1
        task = {"id": name, "site": site}
        queue2.push(task)
        pushed[name] = task

    sequence = []

    def take():
        clock2.now += 1
        task = queue2.next()
        sequence.append(str(task["id"] if task else None))

    take()  # slow1 出队，slow 在途占满
    take()  # fast1 出队，fast 在途也占满
    take()  # 两个站点都达到配额 -> None
    queue2.done(pushed["fast1"])  # fast 完成，释放名额
    take()  # fast2 出队；slow 仍被配额挡住
    take()  # slow 未释放 -> None
    queue2.done(pushed["slow1"])  # 慢站终于完成，释放名额
    take()  # slow2 出队
    print("出队顺序:", " ".join(sequence))


if __name__ == "__main__":
    main()
