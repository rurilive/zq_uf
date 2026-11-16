from corpusmin import add, minimize, report, restore

CORPUS = [
    {"id": "seed-01", "project": "pdf", "size": 120,
     "coverage": ["parser:header", "parser:body", "xref:read"]},
    {"id": "seed-02", "project": "pdf", "size": 80,
     "coverage": ["parser:body"]},  # 覆盖被 seed-01 包含，会被收敛掉
    {"id": "seed-03", "project": "img", "size": 40,
     "coverage": ["decode:png", "decode:png:filter"]},
]

result = minimize(CORPUS)
print(result["kept"])           # 保留的语料
print(result["dropped"])        # 被收敛掉的语料
print(result["coverage_before"], result["coverage_after"])  # 覆盖不变
print(result["archive"])        # 丢弃语料的去向

body = report("pdf")
print(body["projects"]["pdf"]["solo_regions"])  # 只靠一份语料撑着的区域

# 第二段演示：丢错了能取回，新语料统计新增覆盖
print(restore("pdf", ["seed-02"]))
print(add([{"id": "seed-04", "project": "pdf", "size": 60,
            "coverage": ["parser:body", "encrypt:aes"]}]))
print(report()["total"])
