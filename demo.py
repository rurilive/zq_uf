from pprint import pprint

from ruletemplate import (RuleTemplate, apply, bind_dataset, migrate_dataset,
                          register_template)

# 公共模板：两个数据集共用，各自只写字段绑定和差异。
register_template(RuleTemplate("common", 1, [
    {"name": "not_null", "field": "id"},
    {"name": "range", "field": "amount", "min": 0},
]))

bind_dataset("orders", "common",
             fields={"id": "order_id", "amount": "total"},
             overrides=[{"name": "range", "field": "amount", "min": 0, "max": 10000}])
bind_dataset("users", "common",
             fields={"id": "user_id"},
             overrides=[{"name": "not_null", "field": "email"}])

print("== orders 最终规则 ==")
pprint(apply("orders", [])["rules"])
print("== users 覆盖链 ==")
pprint(apply("users", [])["override_chain"])

# 模板升级 v2，已有数据集可迁移并回测影响。
register_template(RuleTemplate("common", 2, [
    {"name": "range", "field": "amount", "min": 0, "max": 100000},
    {"name": "not_null", "field": "created_at"},
], parent=RuleTemplate("common", 1, [
    {"name": "not_null", "field": "id"},
    {"name": "range", "field": "amount", "min": 0},
])))

print("== orders 迁移差异（v1 -> v2） ==")
pprint(apply("orders", [])["migration"])
rows = [{"order_id": 1, "total": 10}, {"order_id": None, "total": 200000}]
print("== orders 迁移 + 回测影响 ==")
pprint(migrate_dataset("orders", 2, rows=rows))
