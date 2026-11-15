# partretention

分区保留：按天清理过期分区，沿血缘级联，纯 Python 标准库。

## 保留与级联口径

- `plan(dataset, catalog=...)` 是干跑：按天列过期分区，沿 `catalog["lineage"]` 标记下游（深度上限默认 3，可配），只输出影响范围不删数据；计划为纯 JSON，可 `save_plan`/`load_plan` 保存后再次执行。
- 租约从目录服务（catalog dict）读取：`leases` 非空的表跳过；`schema_changed` 的下游跳过并告警；下游只级联与过期天重合的分区。
- `apply(plan)` 按 `batch_size`（可配，默认 100）分批删除，失败即停批；`rollback(result, i)` 单独回滚某批；`apply(plan, dry_run=True)` 只演练不删。
- 删除前按天归档到 `archive_dir/<天>/<表>.json`；结果写 `audit_path`（JSONL）并按数据集汇总在 `audit_summary`；输出含 deleted / cascaded / skipped / batches。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
