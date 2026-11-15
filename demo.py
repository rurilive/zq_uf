from dqcontract import check, enforce

batch = {"rows": [{"id": 1}, {"id": None}]}
result = check(batch, {"required": ["id"]})
print(result)
print(enforce(result))

# TODO: 第二段演示（实现后补）
