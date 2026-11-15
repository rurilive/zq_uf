from schemagate import apply, check

# 1. 破坏性变更：门禁阻断并给出迁移建议
r1 = check({"id": "int", "name": "text"}, {"id": "text"})
print("破坏性变更放行:", r1["allowed"], "| 建议:", r1["breaking"][0]["suggestion"])
print("阻断结果:", apply({"tables": ["orders"], "check": r1,
                        "approver": "zhangsan", "reason": "精简字段"})["blocked"])

# 2. 兼容变更：按表比例灰度，审批留痕
r2 = check({"id": "int"}, {"id": "int", "email": "text"})
out = apply({"tables": ["orders", "users", "logs", "audit"], "check": r2,
             "gray_ratio": 0.5, "approver": "lisi", "reason": "新增邮箱列"})
print("灰度表:", out["gray"], "| 全量:", out["applied"], "| 版本:", out["version"])

# 3. 模拟一次失败灰度：自动回滚到上一版本
r3 = check({"id": "int"}, {"id": "float"})
out = apply({"tables": ["orders", "users"], "check": r3, "gray_ratio": 0.5,
             "fail_tables": ["orders"], "approver": "lisi", "reason": "id 放宽为 float"})
print("灰度失败回滚:", out["rolled_back"], "| 审计:")
for e in out["audit"]:
    print(" ", e["action"], e.get("summary", e.get("restored", "")))
