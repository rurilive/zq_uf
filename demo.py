from taskqueue import TaskQueue

queue = TaskQueue()
for name in ("普通单据", "加急单据", "普通单据二"):
    queue.push(name)

print(queue.lease("worker-1"))
print(queue.lease("worker-2"))
print("剩余", queue.pending)

# TODO: 第二段演示（实现后补）
