# jobbudget

数据任务的时间预算模块，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 预算口径

- 总预算池 `Scheduler(total_budget)`，按任务类型的历史耗时均值（`History`）估计需求，无历史时按 `TIMEOUT=60`。
- 每次 `start` 按优先级从高到低重排分配：高优先先拿满估计值，剩余给低优先；分配变化写 `budget_change` 审计。
- 字符串任务名走兼容路径：统一 60s、不保存进度（旧行为不变）。

## 降级口径

- 剩余预算不足估计值时按 `policy` 处理：`reduce` 拿剩余预算降级执行（原因 `budget_shortage`），`fail` 预算为 0 提前失败（原因 `budget_exhausted`）。
- `policy` 可按优先级调整：callable 或 `{priority: 策略, "*": 兜底}`；降级/失败写 `degraded`/`fail_fast` 审计。
- `checkpoint` 保存进度；超时归因三类：`degraded_budget_shortage`、`estimate_underestimated`、`budget_exhausted`；同名 `start` 从断点恢复。

（实现后补，10 行以内）
