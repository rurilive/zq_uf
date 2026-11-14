from taskfair import next_task, submit

submit("big", {"id": 1})
submit("small", {"id": 2})
print(next_task())

# TODO: 第二段演示（实现后补）
