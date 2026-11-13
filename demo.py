from schemacheck import validate

ROWS = [
    {"id": "1", "name": "甲", "amount": "10"},
    {"id": "2", "name": "", "amount": "20"},
]
result = validate(ROWS, {"required": ["id", "name"]})
print("通过", len(result["ok"]))
print("拒绝", result["bad"])

# TODO: 第二段演示（实现后补）
