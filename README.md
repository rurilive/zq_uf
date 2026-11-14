# restoredrill

数据恢复演练模块（纯 Python 标准库）：把快照隔离恢复到临时目录，
校验行数、校验和与关键表，输出演练结论与 RPO/RTO 指标。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法

```python
from restoredrill import configure, drill, report

configure(snapshot_root="/backups", production_dir="/srv/data",
          archive_path="/var/log/restoredrill.jsonl")
result = drill("snap-20261115")   # 隔离恢复 + 校验，返回演练记录
report()                          # 全部演练汇总
report(snapshot="snap-20261115")  # 按快照查询
report(table="users")             # 按表查询
```

快照目录格式：`<快照>/manifest.json`（tables、key_tables、created_at、
每表 chunks/rows/sha256）+ `<快照>/chunks/<块文件>`。
找不到快照目录时按逻辑演练登记成功（兼容旧用法），不恢复也不校验。

## 临时目录与指标口径

- 临时目录：每次演练用 `tempfile.mkdtemp` 建隔离目录，把各表块重建为
  表文件后校验；绝不写生产目录（可配 `production_dir` 做兜底防护）。
  成功即清理；失败保留现场（`kept_scene=True`，`target` 为现场路径）。
- RTO：`rto_seconds`，从快照块重建全部表文件的耗时（秒）。
- RPO：`rpo_seconds`，演练开始时刻 − 快照 `created_at`
  （清单缺失时取快照内最新文件 mtime）。
- 校验：逐表比对行数与 sha256；缺块记 `missing-chunk`，不一致记
  `row-mismatch`/`checksum-mismatch`，差异写入 `diffs`，结论为 FAIL。

## 留档

每次演练追加一行 JSON 到留档文件（默认 `$RESTOREDRILL_ARCHIVE`，
否则 `<系统临时目录>/restoredrill_archive.jsonl`），可用
`configure(archive_path=...)` 指定；留档失败不影响演练结论。
