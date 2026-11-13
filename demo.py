from queuefresh import Queue

q = Queue()
q.push({"id": "hot", "priority": 1})
q.push({"id": "archive", "priority": 9})
q.push({"id": "hot-2", "priority": 1})
print(q.pop()["id"], q.pop()["id"], q.pop()["id"])

# TODO: 第二段演示（实现后补）
