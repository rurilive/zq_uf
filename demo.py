from metricdedup import flush, put

put({"name": "cpu", "ts": 1})
put({"name": "cpu", "ts": 1})
print(flush())

# TODO: 第二段演示（实现后补）
