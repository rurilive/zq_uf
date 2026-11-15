from jsonschema2 import repair, validate

print(validate({"id": 1}, {"required": ["id", "title"]}))
print(validate({"id": 1, "title": "ok"}, {"required": ["id", "title"]}))
print(repair({"id": 1}))

# TODO: 第二段演示（实现后补）
