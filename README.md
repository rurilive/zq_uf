# partgc2

带租约的分区回收，纯 Python 标准库。

租约口径：`pin(partition, holder, ttl, kind)` 登记持有者与到期时间；活跃租约
（含 `kind="backup"` 的未完成备份）保护分区不被回收；到期自动释放；
`release(partition, holder)` 提前释放，holder 不匹配不生效。并发备份先
`pin(kind="backup")` 登记再写。

回收口径：`gc(keep)` 只删 `existing` 中不在 `keep` 且无活跃租约的分区，
删除前复查租约；`batch_size` 分批，`pause`（Event 或回调）批间暂停，
`retries` 控制失败重试并记录到 `failed`。批次结果写
`state_dir/gc-batches-<run_id>.jsonl`，回收后更新 `catalog.json`，
审计按数据集（分区名 `/` 前缀，缺省 `default`）汇总写 `audit.jsonl`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
