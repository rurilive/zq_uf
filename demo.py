from qualityscore import score, trend

score({"dataset": "orders", "rules": [{"failed": False}, {"failed": True}]})
print(trend("orders"))

# TODO: 第二段演示（实现后补）
