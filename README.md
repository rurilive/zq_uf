# retrybudget

采集链路的可传递重试预算：同一链路共享一个预算池，耗尽即快速失败。

## 预算分配

- `with_budget(task, n)` 开启新链路，预算池共 n 次尝试。
- `with_budget(child, parent_task)` 继承父任务的共享池，child["budget"] 为挂载时剩余。
- 每次 `attempt` 原子扣 1；单任务另有 MAX_ATTEMPTS=3 的上限。
- 兄弟调用共享同一池，合计不超过池子，不能透支。

## 放弃口径

- 池子耗尽时 `attempt` 立即返回 retry=False、exhausted=True，不再排队。
- 耗尽结果带 abandoned_at（首次放弃的层级）和 amplification（放行次数/根请求数）。
- `budget_report(task)` 随时输出完整统计；未挂预算或参数非法抛 BudgetError。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
