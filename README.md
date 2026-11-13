# partitiongc

带保护的历史分区批处理清理，仅依赖 Python 标准库。

## 分区识别

- 分区时间来自分区目录内 `partition.json`，不依赖目录名；依次读取
  `event_time`、`timestamp`、`date` 字段，支持 epoch 秒（数字或数字字符串）
  和 ISO 8601 字符串（如 `2020-01-15T10:00:00+00:00`、`2020-01-15`）。
- 读不到有效时间字段时回退到目录修改时间，条目标记 `time_source` 为 `mtime`。
- `partition.json` 可选 `dataset` 字段；也识别目录名中的 `dataset=<name>` 片段，
  缺省数据集为 `default`。

```json
{"dataset": "orders", "event_time": "2020-01-15T10:00:00+00:00"}
```

## 保留策略

- `plan(root, keep_days)`：`keep_days` 为整数时对所有数据集生效；
  为字典时按数据集覆盖，`default` 键兜底。数据时间早于
  `now - keep_days * 86400` 的分区进入计划。

```python
plan(root, keep_days=30)
plan(root, keep_days={"orders": 7, "default": 30})
```

## 租约格式

下游读取前在分区下创建租约文件 `<partition>/.leases/<reader>.json`，
`expires_at` 为 epoch 秒；当前时间早于它即视为活跃读取，该分区跳过并记录
`lease_active`。租约文件缺失或无法解析时按有效处理（失败保护），过期租约
不阻止删除。

```json
{"reader": "downstream-etl", "expires_at": 1893456000}
```

## 分批与审计

- `plan(..., batch_size=100)` 限制每批删除数量；`apply(plan)` 逐批提交，
  返回值继承 `list`，即实际删除路径，另含 `skipped`（含原因）和 `batches`。
- 传入 `audit_path` 后写入 JSONL 审计日志，事件类型为 `plan`、`delete`、
  `skip`、`batch`（含每批删除/跳过计数）。
- 旧接口兼容：`apply([path, ...])` 仍返回删除路径列表。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
