from fieldlineage import record, reset, trace

reset()
record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
        "columns": [{"from": "raw.orders.amount", "to": "dwd.orders.total"}]})
print(trace("dwd.orders"))

# TODO: 第二段演示（实现后补）
