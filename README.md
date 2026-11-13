# tiering

按访问频率和最近访问时间把数据分热、温、冷三层，生成可干跑的迁移计划并校验。

## 分层阈值

阈值常量在 `tiering/tier.py` 顶部，无访问记录的文件默认热层：

- 热层：命中 >= `HOT_MIN_HITS`(10) 且最近访问距今 <= `HOT_MAX_IDLE_DAYS`(7) 天
- 冷层：命中 <= `COLD_MAX_HITS`(2) 或最近访问距今 >= `COLD_MIN_IDLE_DAYS`(90) 天
- 温层：其余
- `record_access(dataset, name, hits=1, at=None)` 记录访问，写入 `.tiering_stats.json`

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 迁移口径

- `plan(dataset, dry_run=False, now=None)` 扫描数据集生成 moves；`apply(plan)` 执行
- 热层常驻数据集根目录，温/冷层迁入 `warm/`、`cold/` 子目录，升温则迁回根目录
- 干跑（`dry_run=True`）只返回 `would_move`/`freed_bytes`，不动磁盘
- 实迁逐条校验目标存在且大小一致，更新 `.tiering_catalog.json`
- 返回 `moved`/`skipped`/`freed_bytes`，`freed_bytes` 只计从热层迁出的字节
- 目录缺失、源文件丢失、目标已存在、统计文件损坏等抛 `TieringError`，不静默跳过

