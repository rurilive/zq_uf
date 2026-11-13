from datacontract import Registry

registry = Registry()
print(registry.register({"name": "orders", "fields": ["id", "amount"]}))
print(registry.diff(["id"], ["id", "amount"]))

# TODO: 第二段演示（实现后补）
