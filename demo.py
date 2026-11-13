from driftcheck import check

print(check("orders", {"value": 130, "baseline": 100}))
print(check("orders", {"value": 105, "baseline": 100}))

# TODO: 第二段演示（实现后补）
