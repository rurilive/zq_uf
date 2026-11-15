# sitemapinc2

现状：每个分片直接覆盖整个索引。

问题：大站全量下载慢，分片互相覆盖，上游删除不消失。

目标：按 lastmod 增量合并，删除留墓碑，分片幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：`merge_shard(shard, seq, {url: lastmod})` 按序号幂等合并，返回
added/updated/deleted/skipped；`diff(round_id)` 回看某轮差异；
`verify(全量, max_diff)` 超限即停，`confirm()` 恢复；`rollback(since)`
回滚删除，`purge(before)` 清理到期墓碑。
