# partlife

现状：按天列出过期分区，一次全部删除。

问题：冷热一个策略，下游在读也删，归档和删除不分。

目标：按访问热度分层，活跃租约跳过，归档后删除并分批回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：`plan(dataset, policy)` 按 `last_access_days` 分 hot/cold/archive 三层，
有活跃读租约的分区进 `skipped`；`apply(plan, store)` 把 cold 层迁入冷存储，
archive 层先归档（复制到 `archive://`，留恢复入口）再按 `batch_size` 分批删除，
任一分区失败则回滚本批次并记为 `rolled_back`，`retry_batch` 单独重试。
归档和删除分别记在 `store.archive_log` / `store.delete_log`。
`query_cold` / `query_archive` 按分区范围查冷层和归档位置，
`recover` 从归档恢复到热层并校验行数。试运行见 `demo.py`（orders 数据集）。
