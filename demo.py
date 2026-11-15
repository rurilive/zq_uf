from schemagate import apply, check

print(check({"id": "int"}, {"id": "text"}))
print(apply({"tables": ["orders", "users"]}))

# TODO: 第二段演示（实现后补）
