# jobbudget3

现状：所有任务固定一小时预算，降级不记录。

问题：大任务被误杀，预算耗尽仍全速跑，恢复从头。

目标：按优先级分配，剩余不足降级，进度落盘可续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 实现要点（10 行以内）

1. `policy.allocate` 按任务类型基价 + 历史耗时 p90×1.2 + 优先级倍率算出预算，替代固定 3600s。
2. `BudgetManager.start` 检查资源池：不足则 `reduce_budget`（少给）或 `early_fail`（提前失败），原因写账本。
3. `checkpoint` 落盘进度，剩余 ≤25% 降为 `conserve`、≤10% 降为 `minimal`，写原因并去重记录。
4. 超时立即停止执行（`halted=True` / `BudgetExhausted`），不再全速空转。
5. `resume` 从断点继续，累计 `used_total`，记录 `resume` 动作。
6. 高优任务（priority ≥ 8）可 `topup` 临时追加预算；低优请求被拒并留原因。
7. 所有动作可用 `actions(job)` / `python3 -m jobbudget3 actions` 查询。
8. `shadow=True, shadow_days=7` 影子运行一周：只记录 `shadow:*` 本应动作，不拦截。
9. `shadow_report` 对比预算与实际耗时，列出 `would_timeout` 与 `false_kill_risk`。
10. 状态原子写入 `.jobbudget3/state.json`（`JOBBUDGET_STATE` / `JOBBUDGET_TOTAL` 可配置）。
