# sitemapinc2

站点地图分片增量合并：替代"每个分片覆盖整个索引"的旧做法。

## 解决的问题

- 大站几十个分片全量下载要几小时 → 按分片增量合并，只处理变化
- 分片互相覆盖 → 同 URL 按 `lastmod` 比较，新者覆盖，相同/更旧跳过
- 上游删除不消失 → 删除落墓碑（tombstone），保留期内可回滚，过期才清理
- 重复下载产生重复结果 → 分片内容签名短路，同一分片重复合并幂等

## 语义

- 分片按序号依次合并；每个 URL 归属于最后携带它的分片
- 分片下载失败只记失败标记，索引不动、不误删，可 `retry_shard` 单独重下
- 删除延迟到整轮结束结算：URL 被同轮其他分片接住（所有权转移）不算删除
- 每轮留存新增/更新/删除/跳过明细，`diff(round_id=...)` 或 `diff(at=时间)` 回看
- 每轮可与全量重建对比，差异超 `verify_threshold` 抛 `DiffThresholdExceeded`
  并阻断墓碑清理，`confirm()` 人工确认后放行

## 用法

```python
from sitemapinc2 import Merger, DiffThresholdExceeded

m = Merger(retention_seconds=7 * 24 * 3600)

# 一整轮：映射的值可以是分片内容、下载函数或 Exception（失败隔离）
summary = m.merge_round({1: {"https://a/1": 10}, 2: ConnectionError(...)},
                        verify_threshold=0)
summary["stats"]  # {'added': n, 'updated': n, 'deleted': n, 'skipped': n}

m.retry_shard(2, {"https://a/2": 10})          # 单独重下失败分片
m.diff(round_id=summary["round_id"])           # 回看某轮差异
m.diff(at=1700000000)                           # 按时间回看
m.rollback(since=t0)                            # 按时间回滚删除
m.purge()                                       # 清理过保留期的墓碑
```

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
