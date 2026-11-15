"""跑一批混合数据：好行、缺字段、类型错、范围错、跨字段规则违反。"""

from crawlverify import audit_log, quarantine, reverify, rollback, verify

SCHEMA = {
    "batch": "batch-demo",
    "required": ["id", "title", "price", "start", "end"],
    "fields": {
        "id": {"type": "int", "min": 1},
        "title": {"type": "str"},
        "price": {"type": "number", "min": 0},
    },
    "rules": [{"name": "period", "left": "start", "op": "<=", "right": "end"}],
    "sample_rate": 0.5,
    "seed": 42,
}

rows = [
    {"id": 1, "title": "正常-a", "price": 9.9, "start": 1, "end": 2, "site": "a"},
    {"id": 2, "title": "正常-b", "price": 5.0, "start": 1, "end": 3, "site": "b"},
    {"id": 3, "site": "a"},                                        # 缺字段
    {"id": 4, "title": 123, "price": 1.0, "start": 1, "end": 2, "site": "a"},   # 类型错
    {"id": 5, "title": "负价", "price": -2, "start": 1, "end": 2, "site": "b"},  # 范围错
    {"id": 6, "title": "倒挂", "price": 3.0, "start": 9, "end": 2, "site": "b"},  # 跨字段
]

result = verify(rows, SCHEMA)
print("== 核对 ==")
print("通过:", result["passed"])
for item in result["isolated"]:
    print("隔离: 行%d 原因=%s 明细=%s" % (item["index"], item["reason"], item["failures"]))
print("抽样:", [(s["index"], s["stratum"]) for s in result["sampled"]])

print("\n== 局部回退（只退回坏行，好行入库） ==")
out = rollback(result)
print("回退:", out["rolled_back"], "保留:", out["kept"])

print("\n== 修复后重新核对 ==")
# 行 4 类型错：title 改回字符串；行 5 负价：改成 2.0
fix = reverify("batch-demo", SCHEMA, fixes={3: {"title": "修好"}, 4: {"price": 2.0}})
print("释放:", fix["released"], "仍隔离:", fix["remaining"])

print("\n== 隔离区（原始行 + 核对轨迹） ==")
for e in quarantine("batch-demo"):
    print("行%d 状态=%s 原始行=%s" % (e["index"], e["status"], e["row"]))
    print("  轨迹:", [(t["check"], t["ok"]) for t in e["trace"]])

print("\n== 回退审计 ==")
for record in audit_log():
    print(record)
