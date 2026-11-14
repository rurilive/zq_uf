"""三天数据演示：加权评分、趋势环比、连续下降告警与留档。"""

import json
import os

from qualityscore import configure, report, score, trend

DATASET = "orders"
ROWS = 10000

# 规则带权重和级别：critical 权重高，minor 权重低
RULES = [
    {"name": "order_id_not_null", "level": "critical", "weight": 3.0},
    {"name": "amount_positive", "level": "major", "weight": 2.0},
    {"name": "email_format", "level": "minor", "weight": 1.0},
]

# 三天各规则的失败行数：连续恶化，第三天触发阈值与连续下降告警
DAILY_FAILED = {
    "2026-11-13": [0, 100, 500],
    "2026-11-14": [50, 400, 1500],
    "2026-11-15": [300, 1200, 4000],
}

configure(threshold=0.9, decline_days=2, window=7)

for day, failed_counts in DAILY_FAILED.items():
    rules = [dict(rule, failed=failed) for rule, failed in zip(RULES, failed_counts)]
    result = score({"dataset": DATASET, "date": day, "rows": ROWS, "rules": rules})
    print("[%s] 总分 %.2f  环比 %+.2f (%s)" % (
        day, result["score"], result["trend"]["delta"], result["trend"]["direction"]))
    for item in result["breakdown"]:
        print("  - %-18s level=%-8s weight=%.1f failed=%4d 得分 %.2f 贡献 %.2f" % (
            item["name"], item["level"], item["weight"],
            item["failed"], item["score"], item["contribution"]))
    for alert in result["alerts"]:
        print("  ! 告警[%s] %s" % (alert["type"], alert["message"]))
    for driver in result["trend"]["drivers"]:
        print("  ↓ 下钻: %s 较前日 %+.2f" % (driver["name"], driver["delta"]))

print("\n趋势(全部):", trend(DATASET))
print("趋势(近2天):", trend(DATASET, days=2))

# 留档：完整报告（每日总分+分项、趋势、告警、配置）写入 reports/
os.makedirs("reports", exist_ok=True)
path = os.path.join("reports", "%s-report.json" % DATASET)
with open(path, "w", encoding="utf-8") as f:
    json.dump(report(DATASET), f, ensure_ascii=False, indent=2)
print("报告已留档:", path)
