# tablettl

按天过期分区的归档与删除模块，纯 Python 标准库。

- `plan(table, partitions, keep_days=30, batch_size=10)`：挑出 `age_days > keep_days` 的分区，返回交给 `apply` 的计划。
- `apply(plan)`：先归档、后删除，返回 `archived` / `deleted` / `skipped` / `batches`。
- `restore(table, partition=None)`：按归档清单回迁并核对，返回每个分区的恢复结果。
- `restore_status` / `verify_restore`：按分区查询恢复结果、核对当前分区。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 归档口径

每个待删分区在删除前写入归档清单，清单包含 `row_count`、`checksum`（行数据规范化 JSON 的 SHA-256）和 `metadata`。清单写入后立即读回并重新计算校验，校验失败的分区记 `archive_failed`，不删除。归档成功后才进入删除阶段。

## 批次口径

删除按 `batch_size` 分批，`batches` 每项记录 `batch` 序号、`partitions`、`deleted` 和 `status`。某分区删除失败时该批标记 `failed` 并记录 `failed` 分区，本批剩余分区和后续批次都记 `delete_stopped`，不再删除。

## 跳过与恢复

下游未确认（`downstream_confirmed=False`）的分区跳过，记 `downstream_unconfirmed`。恢复按清单回迁后重新计算行数和校验和；不一致时撤销回迁、保留原分区和清单，结果记 `restore_verify_failed`。恢复结果可按分区查询和核对。
