# sitemapinc2

站点地图分片增量合并，纯 Python 标准库。

## 合并与墓碑口径

- `merge(old, new, shard_seq=None, now=None, tombstone_ttl=None)`：同 URL 按 lastmod 比较，新值更晚才覆盖（updated），不落后则跳过（skipped）。
- old 有、new 没有的存活 URL 记删除（deleted）并留墓碑：不进 `total`，计入 `tombstones`；重复删除幂等；lastmod 变晚可复活为 added。
- 墓碑保留 `tombstone_ttl` 秒（`configure(tombstone_ttl=)` 配置，默认 7 天），过期清理计入 `expired`。
- 分片按 `shard_seq` 序号合并：乱序到达按序号重放，同序号同内容幂等，`new=None` 表示下载失败、保留旧索引可单独重下；分片层由存储快照重放，结果与全量重建一致。
- 返回 `{total, added, updated, deleted, skipped, expired, tombstones}`；`diff()` 返回上一次合并的四类 URL 列表；`snapshot()` 返回存活索引。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
