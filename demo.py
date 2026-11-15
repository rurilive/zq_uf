from sitemapinc2 import (confirm, diff, history, merge_shard, purge, reset,
                         rollback, snapshot, verify)

reset()

# 分片 0、1 首轮合并
print(merge_shard("s0", 1, {"https://a/1": 10, "https://a/2": 10}, now=1000))
print(merge_shard("s1", 1, {"https://a/2": 20, "https://a/3": 20}, now=1001))

# 重复下载同一分片：幂等，不产生重复结果
print(merge_shard("s1", 1, {"https://a/2": 20, "https://a/3": 20}, now=1002))

# 分片 0 第二轮：/1 被上游删除，/2 lastmod 比 s1 的旧，跳过
print(merge_shard("s0", 2, {"https://a/2": 15}, now=1003))

print("最近一轮差异:", diff())
print("历史:", history())
print("当前索引:", snapshot())

# 与全量重建对比；差异超限会暂停，人工确认后恢复
print(verify({"https://a/2": 20, "https://a/3": 20}))
print(confirm())

# 误删回滚：恢复 1003 之后删除的地址，再清理 900 之前的墓碑
print(rollback(since=1003))
print(purge(before=900))
print("回滚后索引:", snapshot())
