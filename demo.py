"""长尾数据演示：分层采样 + 误差报告 + 重放。"""
from tablesample import error, sample

# 长尾数据：960 条小单，40 条超大单
rows = [{"id": i, "value": i % 50} for i in range(960)]
rows += [{"id": 960 + i, "value": 1000 + i * 250} for i in range(40)]

plan = {
    "stratify_by": "value",
    "boundaries": [10, 100],          # 分层边界可配置；>=100 为长尾层
    "ratio": 0.2,                      # 默认按比例
    "strata": {">=100": {"size": 20}},  # 长尾层固定条数，比例与条数混用
    "query": "订单金额分布审计",        # 种子按查询条件生成
    "confidence": 0.95,
}

picked = sample(rows, plan)
print("== 分层 ==")
for name, info in picked["strata"].items():
    tail = "（长尾）" if info["long_tail"] else ""
    print("  %s%s: 总体 %d 条，抽 %d 条，规则 %s" % (name, tail, info["population"], info["sampled"], info["rule"]))
    if info["warning"]:
        print("    提示: %s" % info["warning"])

print("== 样本（前 5 条，带分层标记）==")
for row in picked["rows"][:5]:
    print(" ", row)

report = error(picked)
stats = report["fields"]["value"]
print("== 分位与误差（value 字段，置信水平 %.0f%%）==" % (report["confidence"] * 100))
print("  均值 %.2f，分位 %s" % (stats["mean"], {k: round(v, 2) for k, v in stats["quantiles"].items()}))
print("  误差范围 [%.2f, %.2f]（±%.2f）" % (stats["error_range"][0], stats["error_range"][1], stats["error"]))

print("== 长尾层单独统计 ==")
for name, entry in report["long_tail"].items():
    value_stats = entry["fields"]["value"]
    print("  %s: n=%d，均值 %.2f，误差范围 [%.2f, %.2f]" % (
        name, entry["n"], value_stats["mean"], value_stats["error_range"][0], value_stats["error_range"][1]))

replayed = sample(rows, picked["plan"])
print("== 重放 ==")
print("  同一计划重放一致:", replayed["rows"] == picked["rows"])
