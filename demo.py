from metastore import Registry

registry = Registry()
registry.register({"name": "orders", "fields": ["id"]})
print(registry.diff({"name": "orders", "fields": ["id", "amount"]}))

# TODO: 第二段演示（实现后补）
