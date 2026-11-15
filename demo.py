"""端到端演示：分片增量合并、失败隔离、墓碑回滚、全量重建对比。

运行：python3 demo.py
"""

from sitemapinc2 import DiffThresholdExceeded, Merger

T0 = 1_000_000  # 演示用固定时钟，保证输出可复现

m = Merger(retention_seconds=86400)

# 第 1 轮：3 个分片，分片 2 下载失败，其余两个正常合并
print("== round 1: shard 2 fails, others merge ==")
r1 = m.merge_round(
    {
        1: {"https://a/1": 10, "https://a/2": 10},
        2: ConnectionError("upstream timeout"),
        3: {"https://a/3": 10},
    },
    now=T0,
)
print(r1["stats"], "failed:", r1["shards"]["failed"])

# 单独重下失败分片（重下本身计为一轮）
print("\n== retry shard 2 alone ==")
print(m.retry_shard(2, {"https://a/2": 10, "https://a/4": 10}, now=T0 + 60))

# 第 2 轮：/2 更新、/4 上游删除、分片 2 内容与上轮相同（幂等短路）
print("\n== round 2: update, tombstone, identical redownload ==")
r2 = m.merge_round(
    {
        1: {"https://a/1": 10, "https://a/2": 20},
        2: {"https://a/2": 20},
        3: {"https://a/3": 10},
    },
    now=T0 + 3600,
    verify_threshold=0,
)
print(r2["stats"])
print("deletions kept:", list(m.deletions()))

# 按轮次号回看这一轮的逐条差异
print("\n== review round 2 changes ==")
d = m.diff(round_id=r2["round_id"])
for kind in ("added", "updated", "deleted", "skipped"):
    print(kind, [c["url"] for c in d["changes"][kind]])

# 全量重建对比：人为制造一条漂移数据后触发阈值阻断，人工确认后放行
print("\n== rebuild verification blocks on drift, then human confirms ==")
m.index["https://a/ghost"] = {
    "lastmod": 1, "shard": 1, "first_seen": T0, "updated_at": T0
}
try:
    m.merge_round(
        {
            1: {"https://a/1": 10, "https://a/2": 20},
            2: {"https://a/2": 20},
            3: {"https://a/3": 10},
        },
        now=T0 + 7200,
        verify_threshold=0,
    )
except DiffThresholdExceeded as exc:
    print("blocked:", exc.report["diff_count"], "divergence ->", exc.report["extra"])
    del m.index["https://a/ghost"]  # 人工核查后处理掉漂移
    m.confirm()
    print("confirmed, blocked_round =", m.blocked_round_id)

# 按时间回滚删除记录（/4 在保留期内恢复）
print("\n== rollback the deletion by time window ==")
print(m.rollback(since=T0 + 3600, now=T0 + 10800))

# 保留期内墓碑不可清理，过期后才清理
print("\n== purge only after retention ==")
print("within retention:", m.purge(now=T0 + 10800)["purged"])
print("after retention:", m.purge(now=T0 + 10800 + 86400 + 1)["purged"])
print("\nfinal index:", sorted(m.index))
