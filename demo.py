"""演示：增量合并、上游删页留墓碑、分片乱序/重复/失败、墓碑过期。"""
from sitemapinc2 import configure, diff, merge, reset, snapshot

reset()
configure(tombstone_ttl=3600)  # 墓碑保留 1 小时

# 1. 首次全量
print("首次全量:", merge({}, {"https://a/1": 1000, "https://a/2": 1000}, now=1000))

# 2. 上游删除 /1、更新 /2 —— 删除留墓碑，不计入 total
print("上游删页:", merge({"https://a/1": 1000, "https://a/2": 1000},
                       {"https://a/2": 2000}, now=2000))
print("本次差异:", diff())
print("存活索引:", snapshot())

# 3. 墓碑过期清理
print("墓碑过期:", merge({}, {}, now=2000 + 3601))

# 4. 分片：乱序到达按序号重放，重复分片幂等，失败保留旧索引
reset()
print("分片2先到:", merge({}, {"https://b/2": 100}, shard_seq=2, now=100))
print("分片1后到:", merge({}, {"https://b/1": 100}, shard_seq=1, now=200))
print("重复分片1:", merge({}, {"https://b/1": 100}, shard_seq=1, now=300))
print("分片2失败:", merge(None, None, shard_seq=2, now=400))
print("最终索引:", snapshot())
