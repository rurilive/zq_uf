# jobbudget2

任务时间预算模块，纯标准库。接口：`start(job)` / `checkpoint(job, progress)`，
`job` 为名字字符串或 `{"name", "type", "priority"}`。

## 预算口径

- `budget = clamp(预估耗时 × 优先级系数, 1s, 3600s)`。
- 预估耗时：该 `type` 的历史平均耗时（完成时自动记录），无历史用类型默认值 60s。
- 优先级系数：high 2.0 / normal 1.0 / low 0.5，高优多给。
- 每次分配写审计（`budget_assigned`，含类型、优先级、预估值、预算）。

## 降级与恢复口径

- checkpoint 保存进度；按 `done/total` 推算剩余所需，超过剩余预算则降级
  （`status=degraded`，带原因），剩余耗尽则提前失败（`status=failed`），均写审计。
- 失败后再次 `start` 自动从上次断点恢复（`resumed=True`，`resume_from` 为保存的进度），
  恢复结果按任务输出并写审计（`resumed`）。
- 异常路径：未 start 的任务 checkpoint 返回 `saved=False` + `error`；
  非法 job/progress 抛 `BudgetError` 子类。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
