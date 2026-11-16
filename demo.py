from riskrule import evaluate, explain

print(evaluate({"id": "e1", "amount": 1500}))
print(evaluate({"id": "e2", "amount": 10, "country": "RU"}))
print(explain("r1"))

# TODO: 第二段演示（实现后补）
