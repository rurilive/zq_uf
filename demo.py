from metricdedup2 import flush, put

print(put({"key": "pv", "version": 3, "value": 10}))
print(put({"key": "pv", "version": 2, "value": 8}))
print(flush())

# TODO: 第二段演示（实现后补）
