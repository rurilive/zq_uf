# lakechecksum

数据湖文件完整性校验：分块哈希、坏块修复、分区清单同步与审计。纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `verify(path)`：按固定块（默认 4 MiB，`LAKECHECKSUM_BLOCK_SIZE` 可改）算 SHA256，
  返回整文件摘要、块数、变化块下标和每块状态；mtime+size 未变时直接复用缓存，只重算变化块。
- `track(partition, path, replica=None)`：把文件纳入分区清单，记录副本位置。
- `repair(partition)`：逐文件比对清单，坏块从副本按偏移重取写回，返回修复块数、
  剩余坏块和清单差异。
- `drop_partition(partition)`：删除分区并同步清理清单（审计保留）。
- `audit(partition=None)`：按分区查询审计记录。

## 块格式

清单存于 `$LAKECHECKSUM_HOME/partitions/<分区>/manifest.json`（默认 `./.lakechecksum`）：
每个文件记录 `size/mtime/block_size/sha256/replica` 和 `blocks` 数组；
每块为 `{"offset": 起始偏移, "length": 字节数, "sha256": 块摘要}`，偏移按块大小对齐，
最后一块可不足块长。文件级缓存在 `files/` 下，按绝对路径哈希命名。

## 修复口径

坏块 = 重算哈希与清单不一致的块（文件变长的尾部也算）。修复时从 `replica`
同偏移读取对应字节，校验与清单一致后写回原位；全部修齐后按清单长度截断。
文件丢失且有副本则整文件恢复；副本缺失或副本块也对不上则计入 `remaining_bad`。
每次修复把修复前后整文件校验和、修复块数、剩余坏块写入
`partitions/<分区>/audit.jsonl`，修复成功后同步刷新清单与缓存。
