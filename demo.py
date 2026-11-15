from backfill2 import apply, plan

p = plan({"start": "day1", "end": "day30"})
print(p)
print(apply(p))

# TODO: 第二段演示（实现后补）
