# taskhandoff

任务迁移交接：检查点 + 双写幂等确认 + 失败回滚，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 交接阶段

1. `checkpoint`：旧任务落检查点并进入只读。
2. `dual_write`：新任务从检查点继续，与旧任务重叠的结果按幂等键写入。
3. `catch_up`：确认新任务追上旧任务前沿。
4. `stop_old`：确认后停旧任务，任务指向新机器。
5. 任一步失败走 `rollback`：旧任务恢复读写继续，已写结果不回退。

## 幂等口径

- 幂等键为 `task_id#序号`，同一结果重复写只计 `idempotent_hits`，不重复落结果。
- 已写结果不回退：回滚只恢复旧任务，sink 中已写结果保留，重试时命中幂等键。
- 相同 `(from, to)` 的重复交接幂等命中，返回 `stage="duplicate"`，不重复执行。
- 返回含 `phases`（交接阶段）、`idempotent_hits`（幂等命中数）、`rollback_reason`（回滚原因）。
