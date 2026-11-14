from taskhandoff import handoff, running

print(handoff("task-1", "m1", "m3"))
print("now on", running("task-1"))

# TODO: 第二段演示（实现后补）
