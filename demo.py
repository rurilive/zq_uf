"""大小任务混合模拟：装箱、抢占、进度保存、利用率与等待时长。"""

from quotasched import Queue


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def show_util(q):
    u = q.utilization()
    print(f"  利用率: cpu={u['cpu']:.0%} mem={u['mem']:.0%}")


# 第一段：基本用法（保持原有接口）
q = Queue()
q.submit({"id": "big", "cpu": 32, "mem": 64})
q.submit({"id": "small", "cpu": 1, "mem": 2})
print(q.next()["id"], q.next()["id"])

# 第二段：8 CPU / 16 GB 机器上的大小任务混合
clock = FakeClock()
q = Queue(cpu=8, mem=16, clock=clock)

print("\n== 装箱：大任务装不满的缝隙给小任务 ==")
q.submit({"id": "big-etl", "cpu": 6, "mem": 8})
q.submit({"id": "small-sync", "cpu": 2, "mem": 4})
q.submit({"id": "mid-report", "cpu": 4, "mem": 4})
print("  派出:", q.next()["id"])  # big-etl
print("  派出:", q.next()["id"])  # small-sync 装进缝隙
print("  派出:", q.next())        # mid-report 装不下 -> None
show_util(q)                      # cpu=100% mem=75%

print("\n== 抢占：紧急任务插队，保存被抢占任务进度 ==")
q.report("big-etl", 0.6)
q.submit({"id": "urgent-backfill", "cpu": 8, "mem": 16, "priority": 9})
print("  派出:", q.next()["id"])  # urgent-backfill，两个低优先级任务被抢占
print("  等待队列:", [s["id"] for s in q.snapshot()["waiting"]])
q.done("urgent-backfill")
resumed = q.next()  # 同优先级按入队先后恢复
print(f"  恢复: {resumed['id']} 从进度 {resumed['progress']:.0%} 继续")
resumed = q.next()
print(f"  恢复: {resumed['id']} 从进度 {resumed['progress']:.0%} 继续")

print("\n== 等待时长与利用率统计 ==")
clock.advance(3)  # t=3
q.submit({"id": "nightly", "cpu": 2, "mem": 4})
q.done("big-etl")  # 释放 6 CPU / 8 GB
clock.advance(2)  # t=5
print("  派出:", q.next()["id"])  # mid-report，等了 5s
print("  派出:", q.next()["id"])  # nightly，等了 2s
s = q.stats()
print(f"  各任务等待: {s['waits']}")
print(f"  平均等待: {s['avg_wait']:.1f}s  最长等待: {s['max_wait']:.1f}s")
print(f"  抢占次数: {s['preemptions']}")
show_util(q)
