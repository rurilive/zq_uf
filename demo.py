from taskqueue import TaskQueue


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


clock = FakeClock()
queue = TaskQueue(
    default_lease_timeout=10,
    default_max_attempts=2,
    clock=clock,
)

queue.push("报销-普通单", priority=10, flow_name="报销流程")
queue.push("报销-加急单", priority=1, flow_name="报销流程")
queue.push("采购-普通单", flow_name="采购流程")

print("报销流程领取:", queue.lease("报销-worker", "报销流程"))
print("采购流程领取:", queue.lease("采购-worker", "采购流程"))
print("采购流程再次领取:", queue.lease("采购-worker", "采购流程"))

urgent = queue.get_task("报销-加急单")
queue.complete(urgent)
stuck = queue.lease("报销-worker", "报销流程")
print("普通单状态:", queue.get_status(stuck).value)

clock.advance(11)
print("超时回收:", queue.reclaim_expired()[0].payload)
print("回收后状态:", queue.get_status(stuck).value)

stuck = queue.lease("新报销-worker", "报销流程")
clock.advance(11)
queue.reclaim_expired()
print("最终状态:", queue.get_status(stuck).value)
print("死信队列:", queue.dead_letter)
print("流程统计:", queue.stats())
