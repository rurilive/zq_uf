from smallfile import compact, plan

p = plan({"partitions": ["part-day1-hour01", "part-day1-hour02"]})
print(p)
print(compact(p))

# TODO: 第二段演示（实现后补）
