from metricmerge import add, recompute

print(add({"window": "2026-01-01", "value": 1.5}))
print(add({"window": "2026-01-01", "value": 2.5}))
print(recompute("2026-01-01"))

# TODO: 第二段演示（实现后补）
