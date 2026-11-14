# lineagerepair

从任务日志的读写记录推断血缘边，人工确认后写回登记；纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 推断口径（`infer(logs)`）

- 日志字段：`job`（任务）、`batch`（批次，缺省 `default`）、`read`/`write`（字符串或列表）；缺读写字段的记录忽略。
- 先按 `(job, batch)` 归集读集合和写集合，组内每个 read→write 组合产出候选边；同一条边的多组证据合并。
- 证据门槛：仅 1 条日志支持的边证据不足，不产出；至少 2 条日志支持才输出，因此单条日志的 `infer` 仍返回 `[]`。
- 置信度：跨 ≥2 个批次复现 = 高 `0.9`（`level=high`）；同任务同批次内重复 ≥2 条 = 中 `0.7`（`level=medium`）。
- 登记优先：与 `register` 手工登记精确相同的边不再作为推断结果产出。
- 冲突保留：推断边与已登记边同源不同目标时，两侧都保留，推断边标记 `status=conflict` 并在 `conflicts_with` 列出对侧，全部推断项默认待确认（`pending`/`conflict`）。
- 每条推断记录含 `edge`、`confidence`、`level`、`status`、`conflicts_with`、`evidence`（按 job/batch 列出的支撑日志）。

## 确认与回滚（`confirm` / `rollback`）

- `confirm(edge, operator)`：把推断边写入登记表（`source=confirmed`，附带证据快照），并追加一条不可改的审计（操作人、时间、变更前后、冲突对侧）；冲突确认后两侧继续保留。
- `register(edge, operator)`：录入既有手工登记，登记优先于推断。
- `rollback(edge, operator)`：回滚该边最近一次未回滚的 `confirm`/`register`，恢复或移除登记，并追加 `rollback` 审计；无可回滚动作时返回 `rolled_back=False`。
- `registry()` / `audit()` 返回登记与审计快照，`reset()` 清空模块状态（测试隔离用）。
