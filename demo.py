from riskrule import (RuleConflictError, evaluate, explain, get_rules,
                      load_rules, replay)

# 1. 评估与解释：命中规则和取值都回传
print(evaluate({"id": "e1", "ts": 100, "amount": 1500}))
print(evaluate({"id": "e2", "ts": 200, "amount": 10, "country": "RU"}))
print(evaluate({"id": "e3", "ts": 300, "amount": 500, "channel": "app"}))

# 2. 优先级冲突在加载前拦截
conflict = [
    {"id": "a", "priority": 10, "action": "deny",
     "condition": {"field": "amount", "op": ">=", "value": 100}},
    {"id": "b", "priority": 10, "action": "review",
     "condition": {"field": "amount", "op": ">=", "value": 500}},
]
try:
    load_rules(conflict)
except RuleConflictError as exc:
    print("冲突拦截:", exc.warnings[0]["message"])

# 3. 调整阈值（配置变更，不发版），旧版本保留
rules = get_rules()
rules[0]["condition"]["value"] = 2000
print(load_rules(rules, note="阈值 1000→2000"))

# 4. 只回放受影响的时间段，对比调整前后通过率
out = replay(start=0, end=400, compare_to=1)
print("命中分布:", out["hits"], "通过率:", out["pass_rate"],
      "调整前:", out["compare"]["pass_rate"], "变化:", out["compare"]["diff"])

# 5. 按事件解释：结论、命中规则、完整取值、回放结果
print(explain("e1"))
