from ruletemplate import apply

print(apply("orders", [{"name": "not_null", "field": "id"}]))
print(apply("users", [{"name": "not_null", "field": "id"}]))

# TODO: 第二段演示（实现后补）