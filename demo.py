"""模拟总预算不足：高优先拿满，低优先降级/失败，超时后断点恢复。"""

from jobbudget import DEGRADE_FAIL, DEGRADE_REDUCE, History, Scheduler, TaskSpec


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


history = History()
history.record("etl", 80)  # 历史耗时：etl 约 80s
history.record("report", 50)  # report 约 50s

clock = FakeClock()
sched = Scheduler(
    total_budget=100,  # 总预算 100s，装不下 80+50
    history=history,
    policy=lambda p: DEGRADE_FAIL if p == 0 else DEGRADE_REDUCE,  # 最低优先提前失败，其余降级跑
    clock=clock,
)

print("== 预算分配（高优先 report 先拿满，etl 降级，cleanup 失败）==")
print(sched.start(TaskSpec("etl-daily", "etl", priority=5)))
print(sched.start(TaskSpec("report-hourly", "report", priority=9)))
print(sched.start({"name": "cleanup", "task_type": "report", "priority": 0}))
print(sched.allocation())

print("== 被降级/失败的任务 ==")
print(sched.degraded_tasks())

print("== etl 降级预算 50s，跑 60s 超时，进度保存 ==")
clock.advance(60)
print(sched.checkpoint("etl-daily", {"done": 3, "total": 10}))
print(sched.timeout_report())

print("== 断点恢复：重跑从 done=3 继续 ==")
print(sched.start(TaskSpec("etl-daily", "etl", priority=5)))

print("== 审计：预算变化、降级、超时、恢复 ==")
for entry in sched.audit():
    print(entry)
