# txrollback

可回滚的批次写入：提交时记录补偿日志，失败可按批反向补偿，纯标准库实现。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `commit(batch)`：提交一批写入，返回 `{"batch_id", "written"}`。
- `rollback(batch_id)`：按补偿日志反向回滚，返回 `{"batch_id", "compensated", "pending"}`。
- `stats()`：返回 `{"committed", "rolled_back", "pending"}` 行数统计。
- `configure(max_rows_per_batch=N)`：调整单批行数阈值，`reset()` 后恢复默认。
- `PENDING`：待人工队列，补偿失败的行在此等待人工处理。

## 批次格式与参数

- `batch`：`{"id": 批次ID, "rows": [行, ...]}`，`id` 必填且非空，`rows` 必须是 list。
- 行：`{"key": 主键, "op": "insert"|"update"|"delete", ...}`，`op` 缺省为 `insert`。
- `update`/`delete` 要求主键已存在，否则抛 `BatchError`。
- 阈值：`max_rows_per_batch` 默认 1000，超限抛 `BatchError`。

## 补偿日志

- 每行提交时记录 `{key, op, before, after}`（主键、操作类型、前后镜像）到 `JOURNAL[batch_id]`。
- 批次状态：`committed` / `failed`（中途失败，已写行保留）/ `rolled_back`。

## 回滚口径

- `rollback` 按日志逆序把每行恢复为 `before` 镜像。
- 补偿前校验当前值等于 `after`；不一致（被外部修改或删除）则不改动该行，
  记入 `PENDING` 待人工队列（含 `batch_id`、`key`、`op`、`reason`），继续处理其余行。
- 幂等：`commit` 对已提交批次直接返回首次结果；`rollback` 对已回滚批次返回空结果，不重复补偿。
- 错误约定：参数/阈值非法抛 `BatchError`（`ValueError` 子类）；行内 `fail` 抛 `RuntimeError`；
  未知 `batch_id` 的 `rollback` 抛 `NotImplementedError`。异常路径不静默跳过。

## 统计口径

- `committed`：成功写入的行数（幂等重提交不重复计数）。
- `rolled_back`：成功补偿的行数。
- `pending`：进入待人工队列的行数。
