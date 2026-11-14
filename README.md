# quarantine

坏数据隔离与回放模块，纯 Python 标准库。ingest 不再因一条坏行整批回滚：
好行正常入库，坏行连同原始内容和原因进入隔离区，修复后按批幂等重放并对账。

## 隔离口径

- `ingest(rows, batch_id=None)` 逐行校验：好行立即入库，坏行进隔离区，互不影响。
- 隔离记录包含：批次 `batch_id`、原因 `reason`、批次内位置 `position`、
  幂等键 `row_key`、原始内容深拷贝 `raw`、状态 `status`、重放时间 `replayed_at`。
- 校验原因：`flagged_bad`（`bad=True`）、`missing_id`（无 `id`）、`not_a_dict`（非字典行）。
- 隔离区按批次和原因双向索引：`quarantine_list(batch_id=..., reason=...)`。
- 按原因导出原始样本：`export_samples(reason, limit=None)`。
- 返回 `{batch_id, ingested, quarantined, reason_dist}`；`batch_id` 缺省自动生成。
- 行幂等键优先取 `id`，无 `id` 时取整行内容哈希；重复好行只入库一次。
- 历史整批失败语义保留为 `ingest(rows, legacy=True)`（遇坏行即抛 `ValueError`）。

## 回放口径

- `replay(batch_id, fixed_rows=None)` 只重放指定批次的隔离记录；未知批次抛
  `NotImplementedError`。
- 修复行支持三种给法：`(位置, 行)` 按批次位置精确匹配、`(record_id, 行)`、
  或直接给 dict 列表（按 id/内容匹配，匹配不上则按隔离顺序对齐）；
  不传修复行时用隔离区保留的原始内容重放。
- 重放幂等：已入库的行计 `duplicates` 不重复写入；已重放的隔离记录再次重放只计重复。
- 仍不合格的行刷新原因后继续隔离，计 `still_bad`。
- 返回 `{batch_id, replayed, duplicates, still_bad, reason_dist, reconciliation}`。
- 对账字段 `reconciliation`：原批好行 + 本次新入库 + 重复跳过 + 仍隔离
  等于原批总行数时 `balanced=True`，保证每一行都有去向。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

状态保存在模块级内存结构中，`reset()` 可清空（测试用）。
