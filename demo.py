from riskrule import (
    activate,
    compare,
    configure,
    evaluate,
    explain,
    history,
    replay,
    validate,
)

# 1. 默认配置评估：命中多条规则时按优先级取结论，取值一并返回
first = evaluate({"id": "e1", "amount": 1500})
print(first["action"], first["rule"], first["values"])
multi = evaluate({"id": "e2", "amount": 1500, "country": "RU", "channel": "web"})
print("matched:", [item["rule"] for item in multi["matched"]], "values:", multi["values"])

# 2. 按事件查看命中与取值，以及每条规则的取值
event = explain("e2")
print("explain e2:", event["action"], event["rule"], event["snapshot"])
print("per-rule values:", [(item["rule"], item["actual"]) for item in event["matches"]])

# 3. 调整阈值：配置化变更，旧版本保留
evaluate({"id": "e3", "amount": 1800})
evaluate({"id": "e4", "amount": 2500})
configure(
    {
        "version": "v2",
        "rules": [
            {"id": "r1", "priority": 100, "condition": {"field": "amount", "op": ">="},
             "threshold": 2000, "action": "deny"},
            {"id": "r2", "priority": 90, "condition": {"field": "country", "op": "=="},
             "value": "RU", "action": "deny"},
        ],
    }
)
print("versions:", [item["version"] for item in history()])

# 4. 回放与对比：命中分布、通过率变化、被改判事件
print("v2 replay:", replay("v2")["actions"], "pass rate:", replay("v2")["pass_rate"])
diff = compare("v1", "v2")
print("pass rate:", diff["pass_rate"], "changed:", diff["changed_count"])
print("changed events:", diff["changed"])

# 5. 优先级冲突：执行前就能看到，冲突按配置顺序解决
conflicts = validate(
    {
        "rules": [
            {"id": "a", "priority": 50, "condition": {"field": "channel", "op": "=="},
             "value": "web", "action": "review"},
            {"id": "b", "priority": 50, "condition": {"field": "channel", "op": "in"},
             "value": ["web", "h5"], "action": "deny"},
        ]
    }
)
print("conflicts:", [item["message"] for item in conflicts])

# 6. 规则命中分布与命中事件清单
r1 = explain("r1")
print("r1:", r1["condition"], "hits:", r1["replay"]["rule_hits"], "events:", r1["events"])

# 7. 只回放受影响的时间段
print("window events:", replay("v2", start="2026-01-01", end="2026-12-31")["events"])

# 8. 回滚到旧版本，再对比差异
activate("v1")
active = [item["version"] for item in history() if item["active"]]
print("active:", active, "r1 threshold:", explain("r1")["threshold"])
print("v2 vs v1 changed:", compare("v2", "v1")["changed_count"])
