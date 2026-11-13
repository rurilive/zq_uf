"""演示：一批混合数据走完整校验流程（六类规则、依赖、预算、流式、重放）。"""

from schemacheck import replay, validate, validate_stream

RULES = {
    "dataset": "users",
    "required": ["id", "name"],
    "fields": {
        "id": {"type": "int"},
        "name": {"type": "str", "length": {"min": 1, "max": 8}},
        "age": {"type": "int"},
        "role": {"enum": ["admin", "user", "guest"]},
        "email": {"regex": r"[\w.]+@[\w.]+", "depends_on": ["id"]},
        "dept": {"reference": {"dataset": "depts", "key": "id"}},
    },
    "checks": [
        {"id": "date_order", "cross_field": {"left": "start", "op": "<=", "right": "end"}},
    ],
    "datasets": {"depts": [{"id": "D1"}, {"id": "D2"}]},
    "budget": {"users": 0.9, "default": 0.0},
}

ROWS = [
    {"id": "1", "name": "甲", "age": "30", "role": "admin",
     "email": "jia@example.com", "dept": "D1", "start": "2024-01-01", "end": "2024-06-01"},
    {"id": "2", "name": "", "age": "25", "role": "user",
     "email": "yi@example.com", "dept": "D1", "start": "2024-01-01", "end": "2024-02-01"},
    {"id": "x", "name": "丙", "age": "40", "role": "user",
     "email": "not-an-email", "dept": "D2", "start": "2024-01-01", "end": "2024-02-01"},
    {"id": "4", "name": "丁", "age": "35", "role": "root",
     "email": "ding@example.com", "dept": "D1", "start": "2024-01-01", "end": "2024-02-01"},
    {"id": "5", "name": "戊", "age": "28", "role": "guest",
     "email": "wu@example.com", "dept": "D9", "start": "2024-01-01", "end": "2024-02-01"},
    {"id": "6", "name": "己", "age": "33", "role": "user",
     "email": "ji@example.com", "dept": "D2", "start": "2024-07-01", "end": "2024-01-01"},
]

result = validate(ROWS, RULES)
print("== 批量校验 ==")
print("通过:", len(result["ok"]))
for entry in result["bad"]:
    field = entry.get("field") or ",".join(entry.get("missing", []))
    print("  拒绝 行%d 字段=%s 原值=%r 原因=%s"
          % (entry["row"], field, entry.get("value"), entry["reason"]))
for entry in result.get("skipped", []):
    print("  跳过 行%d 规则=%s 原因=%s" % (entry["row"], entry["check"], entry["reason"]))
budget = result["budget"]
print("错误率: %.0f%% (预算 %.0f%%) -> %s"
      % (budget["error_rate"] * 100, budget["max_error_rate"] * 100,
         "整批拒绝" if budget["exceeded"] else "隔离坏记录"))

strict = validate(ROWS, dict(RULES, budget={"users": 0.1}))
print("收紧预算到 10%%: batch_rejected=%s, ok=%d 条"
      % (strict["batch_rejected"], len(strict["ok"])))

print()
print("== 流式校验（生成器逐条产出，不整体加载）==")
for kind, payload in validate_stream(iter(ROWS), RULES):
    if kind == "summary":
        print("  汇总: 共%d行 坏行%d 错误率%.0f%%"
              % (payload["total_rows"], payload["bad_rows"],
                 payload.get("error_rate", 0) * 100))

print()
print("== 样本重放 ==")
again = replay(result["bad"], RULES)
print("重放%d条拒绝样本，再次拒绝%d条"
      % (len({e["row"] for e in result["bad"]}), len(again["bad"])))

