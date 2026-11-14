from rowlineage import record, reset, trace

reset()
record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"]})
print(trace("dwd.orders"))

# TODO: 第二段演示（实现后补）
