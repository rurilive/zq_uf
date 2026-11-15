"""演示分片增量合并：上游删页、分片失败重下、墓碑过期、全量重建一致。"""

from sitemapinc2 import IncrementalIndex, diff, merge

# 原语口径：merge 只合并 new 覆盖到的 URL，不判删。
print("merge 原语:", merge({"https://a/1": 1, "https://a/2": 1},
                          {"https://a/2": 2}))
print("diff:", diff())
print()

# 上游初始两个分片。
index = IncrementalIndex(tombstone_ttl=30)
t0 = 1_000_000
index.merge_shards([
    (0, {"https://a/home": 10, "https://a/news": 10}),
    (1, {"https://a/old": 10, "https://a/shop": 10}),
], now=t0)
print("初始索引:", index.index)

# 上游删掉 /old，更新 /shop；分片 2 下载失败。
stats = index.apply_shard(1, {"https://a/shop": 20}, now=t0 + 60)
print("分片1 重下(删页):", stats)
print("删除传播 diff:", index.diff()["deleted"])
print("分片2 下载失败:", index.apply_shard(2, None, now=t0 + 60))
print("墓碑:", index.tombstones())

# 失败分片单独重下，旧索引全程保留。
print("分片2 重下成功:", index.apply_shard(
    2, {"https://a/blog": 20}, now=t0 + 120))

# 增量结果与全量重建必须一致。
assert index.index == index.rebuild()
print("与全量重建一致:", index.index)

# /old 在上游重新出现（复活，清墓碑）。
index.apply_shard(1, {"https://a/shop": 20, "https://a/old": 30}, now=t0 + 180)
print("old 复活后墓碑:", index.tombstones())

# 再次删除，并演示墓碑 TTL 到期清理。
index.apply_shard(1, {"https://a/shop": 20}, now=t0 + 240)
print("TTL 内清理:", index.cleanup(now=t0 + 260))
print("TTL 过期清理:", index.cleanup(now=t0 + 300))
print("最终索引:", index.index)
