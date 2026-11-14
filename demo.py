"""三天数据演示：加权评分、趋势环比、连续下降告警与下钻。"""

from qualityscore import configure, score, trend

configure(threshold=0.85, decline_days=2, window=7, history_file="history.json")

RULES = [
    {"name": "order_id_not_null", "level": "critical", "weight": 3},
    {"name": "amount_positive", "level": "major", "weight": 2},
    {"name": "user_unique", "level": "minor", "weight": 1},
]

# 三天：失败行数逐天恶化
DAYS = [
    ("2026-11-13", [(0, 1000), (0, 1000), (0, 1000)]),
    ("2026-11-14", [(0, 1000), (50, 1000), (0, 1000)]),
    ("2026-11-15", [(30, 1000), (120, 1000), (10, 1000)]),
]

for day, rows in DAYS:
    rules = [
        dict(rule, failed_rows=failed, total_rows=total)
        for rule, (failed, total) in zip(RULES, rows)
    ]
    report = score({"dataset": "orders", "date": day, "rules": rules})
    t = report["trend"]
    print("%s 总分 %.2f 环比 %+.2f 趋势 %s" % (day, report["score"], t["delta"], t["direction"]))
    for item in report["breakdown"]:
        print("  [%s] %s 权重 %s 得分 %.2f（失败 %d/%d 行）" % (
            item["level"], item["name"], item["weight"],
            item["score"], item["failed_rows"], item["total_rows"]))
    for alert in report["alerts"]:
        print("  告警[%s] %s" % (alert["type"], alert["message"]))
    for driver in report.get("decline_drivers", []):
        print("  下钻: %s 得分 %.2f -> %.2f，加权影响 %.2f" % (
            driver["rule"], driver["from"], driver["to"], driver["impact"]))

print("\n最近窗口总分:", trend("orders"))
print("回看明细:")
for item in trend("orders", detail=True):
    print("  %s 总分 %.2f 环比 %+.2f" % (item["date"], item["score"], item["delta"]))
