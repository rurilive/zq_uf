# sitemapinc

站点地图增量合并模块，纯 Python 标准库，无第三方依赖。

## 合并口径

`merge(old, new)`：`old` 是上一版索引，`new` 是上游这次下载到的快照，返回合并后的索引。

- **同 URL 比 lastmod**：新值 lastmod 更晚才覆盖旧值；lastmod 相同视为同一代，
  直接跳过（旧值原样保留，计入 `skipped`）；lastmod 更早的旧代回放也跳过。
- **缺失即删除**：`old` 里有、`new` 里没有的 URL，视为上游已删除：移出索引并留墓碑。
  分片没下全时（`complete=False`，或 `FetchResult` 有失败片），缺失只当“没覆盖到”，
  保守保留，不传播删除。
- **墓碑**：每个被删 URL 只保留删除前最近一代的条目（`value` + 原始 `lastmod`），
  供 `rollback_last()` 回滚一代。同一 URL 只留一份，重复删除只更新为更近的一代；
  URL 重新出现（复活）时墓碑自动作废。
- **lastmod 格式**：ISO-8601（`2024-01-02`、`2024-01-02T03:04:05Z`、带时区偏移）
  或数值 epoch 秒；无时区按 UTC，跨时区按绝对时刻比较。条目可以是带 `lastmod`
  的映射、`(lastmod, 载荷)` 二元组，或者干脆就是一个时间戳值。
- **没有 lastmod 的条目**（例如裸整数/字符串）沿用旧的全量覆盖口径：值变了就算更新。

## 分片与幂等

- `merge_shards(old, shards)`：把多个分片当同一份快照，先按 lastmod 取并集，再整体
  合并。重复分片、重复合并结果不变，只会全部计入 `skipped`。
- `fetch_shards(shards, fetch, retries=1)`：逐片下载，某片失败只重取该片，其它片不重下；
  返回的 `FetchResult` 记录成功片、失败片与每片尝试次数，`merge_into(old)` 合并时
  自动按“不完整”处理，避免把没下到的 URL 误删。
- `report()` 输出最近一次合并的 `added` / `updated` / `removed` / `skipped` 计数，
  以及当前墓碑总数 `tombstones`；`diff()` 输出这三类 URL 列表。
  `tombstones()` 查看墓碑明细；`rollback_last()` 只回滚最近一次合并里的删除（把本次
  写下的墓碑条目放回索引，更早一代的墓碑不受影响）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
