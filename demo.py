from quotasched import Queue

q = Queue()
q.submit({"id": "big", "cpu": 32, "mem": 64})
q.submit({"id": "small", "cpu": 1, "mem": 2})
print(q.next()["id"], q.next()["id"])

# TODO: 第二段演示（实现后补）