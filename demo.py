from jobbudget import checkpoint, start

print(start("big-etl"))
print(checkpoint("big-etl", {"done": 3}))

# TODO: 第二段演示（实现后补）
