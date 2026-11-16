# riskrule

可配置的风控规则引擎：条件、阈值、动作分开配置，命中给出规则与取值。

- 规则形如 `{"id": "r1", "priority": 100, "condition": {"field": "amount", "op": ">="}, "threshold": 1000, "action": "deny"}`，`op` 支持 `>= > <= < == != in not_in`；`configure(config, version=...)` 生效，版本不可覆盖。
- `evaluate(event)` 返回 `action/rule/values/matched`；多条命中按 `priority` 取结论，同优先级按配置顺序并报告冲突。
- `explain("r1")` 查看规则条件与历史命中分布，`explain("e1")` 查看事件取值与重判结果。
- `replay(version, start=..., end=...)` 按时间段回放，给出通过率与命中分布；`compare("v1", "v2")` 对比通过率与被改判事件。
- `validate(config)` 执行前检查优先级冲突；`history()` 列版本，`activate("v1")` 回滚。

运行：`python3 -m unittest discover -s tests`，演示：`python3 demo.py`。
