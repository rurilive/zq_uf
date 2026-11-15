# retryflow

重试与熔断联动：`retry(task, site)` 在站点熔断时返回 `wait`（不消耗重试次数），
恢复期满进入半开，按阶梯 (1, 2, 4) 放量放行，每阶需连续 2 次成功，走满才闭环；
半开期间任何失败立即重新熔断。`report(task, site, ok, error=)` 回报结果驱动上述状态机。

预算口径：单任务仍最多重试 3 次；站点共享 8 个重试令牌（闭环时成功回补 1 个）；
放大倍数上限 3.0，即站点重试消耗 ≤ 3 × 站点不同任务数，超限 `give_up`。
所有分支返回同一结构（task/site/action/attempts/reason/circuit/budget_remaining/retry_after），
决策与熔断变迁写入日志和 `records()`，超时与拒绝可按任务定位；非法参数抛 TypeError/ValueError。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
