from wafregress import regress, report

rules = [
    {"id": "r1", "pattern": "union select"},
    {"id": "r2", "pattern": "<script>"},
]

print(regress(rules))
print(report()["blocked"])
print(report()["missed"])

# TODO: 第二段演示（实现后补）
