from qtrend2 import check, trend

print(check("orders", {"value": 0.08}))
print(check("orders", {"value": 0.01}))
print(trend("orders"))

# TODO: 第二段演示（实现后补）
