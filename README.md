# riskrule

可配置、可解释的风控规则引擎：条件、阈值、动作分开配置，改配置不发版。

- `evaluate(event)`：返回结论、全部命中规则及取值，多条命中按优先级取结论
- `explain(id)`：按事件查看结论、命中规则、完整取值和回放结果
- `load_rules(rules)`：加载新版本，优先级冲突在执行前拦截，旧版本保留
- `replay(start, end, version, compare_to)`：按时间段回放，给出命中分布与调整前后通过率对比

运行 `python demo.py` 或 `python -m unittest discover tests`。
