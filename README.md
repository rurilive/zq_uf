# tablearchive

现状：所有数据在线，查询只读在线表。

问题：存储成本高，归档要改 SQL，恢复靠人工。

目标：冷热分层，透明路由，合并和恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

冷热分层：热层为内存在线表 `_rows`；`archive(table, {"before": 年份})` 把
早于阈值的行写入冷层 `.cold/` 下的 JSONL 分区，元数据 `_meta`（并落盘
`_meta.json`）记录分区路径、时间字段、起止范围、行数与 SHA-256 校验和；
归档后源行从热层删除。范围参数支持 `before`/`after`（开区间）、
`from`/`to`（闭区间）、`field`（时间字段，默认 `year`）。

透明路由：`query(table, filters)` 接口不变，等值过滤；时间字段等值条件
只扫描命中的冷分区（按 `[start, end]` 裁剪），其余条件全量路由；冷热结果
按写入顺序合并返回，`last_route()` 查看路由。`restore(table, partition=None)`
按元数据回迁：先校验行数与校验和，全部通过才写回热层并删除冷分区，
任一失败则不回迁。
