from retention import apply, plan

p = plan("orders", [{"name": "p1", "age_days": 40}, {"name": "p2", "age_days": 2}])
print(p)
print(apply(p))

# TODO: 第二段演示（实现后补）
