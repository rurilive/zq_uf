from corpusmin import minimize, report

CORPUS = [
    {"id": "seed-01", "project": "pdf", "size": 120},
    {"id": "seed-02", "project": "pdf", "size": 80},
    {"id": "seed-03", "project": "img", "size": 40},
]

result = minimize(CORPUS)
print(result["kept"])
print(result["dropped"])
print(result["coverage_after"])
print(report())

# TODO: 第二段演示（实现后补）
