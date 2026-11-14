import importlib

from taskhandoff import handoff, running

module = importlib.import_module("taskhandoff.handoff")


def seed(task_id, host, written, checkpoint, source):
    module._hosts[task_id] = host
    state = module._state(task_id)
    state.written = written
    state.checkpoint = checkpoint
    state.source = source
    for seq in range(written):
        key = module._idem_key(task_id, seq)
        state.sink[key] = module._payload(task_id, seq)
    return state


# 新任务落后：旧任务已写 5 条，检查点在 3，前沿到 8
seed("task-1", "m1", written=5, checkpoint=3, source=8)
result = handoff("task-1", "m1", "m3")
print("handoff:", result)
print("now on", running("task-1"))

# 追上阶段失败：回滚到旧任务继续，已写结果保留
state = seed("task-2", "m1", written=5, checkpoint=3, source=8)
module._faults["task-2"] = "catch_up"
result = handoff("task-2", "m1", "m3")
print("handoff:", result)
print("task-2 still on", running("task-2"), "sink kept", len(state.sink))
