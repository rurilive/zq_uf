from schemaversion import register, resolve

register({"version": 1, "fields": {"id": "int"}})
register({"version": 2, "fields": {"id": "int", "note": "str"}})
print(resolve())

# TODO: 第二段演示（实现后补）
