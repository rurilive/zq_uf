from dqcontract import check, enforce, failure_samples, recheck, register_contract

schema = {"version": 7, "fields": ["id", "name", "amount", "status"]}

# 第一段：审批登记契约，检查并阻断坏数据
contract = register_contract({
    "version": 3,
    "rules": [
        {"field": "id", "type": "not_null"},
        {"field": "id", "type": "unique"},
        {"field": "name", "type": "not_null"},
        {"field": "amount", "type": "range", "min": 0, "max": 10000},
        {"field": "status", "type": "enum", "values": ["new", "paid"], "severity": "warn"},
    ],
}, schema, approved_by="ops")

batch = {"batch_id": "b-101", "rows": [
    {"id": 1, "name": "a", "amount": 5, "status": "new"},
    {"id": None, "name": "b", "amount": -3, "status": "???"},
]}
result = check(batch, contract)
print(result["ok"], result["passed"], result["quarantined"], result["alerted"],
      result["version"], result["schema_version"])
print(enforce(result))
print(failure_samples(result["batch_id"]))

# 第二段：新规则先影子运行，确认隔离比例后再启用阻断
shadow_contract = register_contract({
    "version": 4,
    "rules": contract["rules"] + [
        {"field": "amount", "type": "range", "min": 1, "mode": "shadow"},
    ],
}, schema, approved_by="ops")
shadow_result = check(batch, shadow_contract)
print("shadow:", shadow_result["shadow"])  # 看隔离比例，可接受再把 mode 改为 active

# 隔离数据修复后重新检查
fixed = [dict(batch["rows"][i], id=2, amount=0) for i in result["quarantined"]]
print("recheck:", recheck(fixed, contract)["ok"])
