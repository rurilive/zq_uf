from jobbudget3 import checkpoint, start

print(start({"name": "backfill", "priority": 9}))
print(checkpoint("backfill", 0.5))

# TODO: 第二段演示（实现后补）
