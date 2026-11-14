"""演示：对比两版表结构的基线差异、分级与告警。"""

from schemadrift import build_alert, diff, filter_report, snapshot

# 第一版（仓库认知的基线）
BASE = {
    "name": "orders",
    "env": "prod",
    "columns": [
        {"name": "id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "numeric(12,2)", "nullable": False},
        {"name": "status", "type": "text", "nullable": False, "default": "new"},
        {"name": "api_token", "type": "text", "nullable": True, "default": "sk-live-012345"},
        {"name": "legacy_code", "type": "text", "nullable": True},
    ],
}

# 第二版（线上被手工变更后的结构）
TARGET = {
    "name": "orders",
    "env": "prod",
    "columns": [
        {"name": "id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "numeric(14,2)", "nullable": False},  # 类型变化
        {"name": "status", "type": "text", "nullable": False},           # 默认值被移除
        {"name": "api_token", "type": "text", "nullable": True, "default": "sk-live-999999"},  # 敏感列
        {"name": "hand_added", "type": "text", "nullable": False},       # 手工新增
        # legacy_code 被删除
    ],
}


def show(title, report):
    print("=" * 60)
    print(title)
    print("指纹: %s -> %s" % (report["base_fingerprint"][:19], report["target_fingerprint"][:19]))
    print("是否有漂移: %s, 最高级别: %s" % (report["has_drift"], report["level"]))
    print("类别统计: %s" % report["summary"])
    for change in report["changes"]:
        line = "  [%s] %s %s" % (change["level"], change["kind"], change["column"])
        if change.get("attribute"):
            line += " (%s)" % change["attribute"]
        if change.get("redacted"):
            line += " [敏感列只比哈希]"
        print(line)
        print("      建议: %s" % change["suggestion"])


base, target = snapshot(BASE), snapshot(TARGET)
report = diff(base, target)
show("全量差异", report)

show("只看敏感字段 api_token", filter_report(report, columns=["api_token"]))
show("只看 high 级别", filter_report(report, min_level="high"))

alert = build_alert(report)
print("=" * 60)
print("prod 告警：")
print(alert["message"] if alert else "(未达到该环境告警门槛)")
