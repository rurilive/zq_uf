from fairqueue import TaskQueue

q = TaskQueue()
for i in range(3):
    q.push({"id": i, "site": "big" if i < 2 else "small"})

while q.size():
    print(q.next())

# TODO: 第二段演示（实现后补）
