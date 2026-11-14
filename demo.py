from metricrollup import add, query

add({"dim": "shop", "ts": 100, "value": 2})
add({"dim": "shop", "ts": 200, "value": 3})
print(query("shop"))

# TODO: 第二段演示（实现后补）
