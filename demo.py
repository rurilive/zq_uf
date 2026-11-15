from metricrollup2 import add, query

print(add({"dim": "shop", "window": "2026-01-01", "value": 1.0}))
print(add({"dim": "shop", "window": "2026-01-01", "value": 3.0}))
print(query("shop", "2026-01-01"))

# TODO: 第二段演示（实现后补）
