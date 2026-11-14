from backfillplan import plan, run

p = plan({"start": 1, "end": 30}, 4)
print(p)
print(run(p, 8))

# TODO: 第二段演示（实现后补）
