from sitemapinc import diff, fetch_shards, merge, report, tombstones

# 第一版站点地图：三个页面
upstream_v1 = {
    "https://example.com/": {"lastmod": "2024-01-01", "title": "首页"},
    "https://example.com/docs": {"lastmod": "2024-01-05", "title": "文档"},
    "https://example.com/old": {"lastmod": "2024-01-09", "title": "旧活动页"},
}

index = merge({}, upstream_v1)
print("v1 入库:", report())

# 第二版：首页更新、旧活动页被上游删除，另新增一个页面
upstream_v2 = {
    "https://example.com/": {"lastmod": "2024-02-01", "title": "首页（改版）"},
    "https://example.com/docs": {"lastmod": "2024-01-05", "title": "文档"},
    "https://example.com/new": {"lastmod": "2024-02-03", "title": "新活动页"},
}

index = merge(index, upstream_v2)
print("v2 增量:", diff())
print("v2 计数:", report())
print("墓  碑:", {url: t["value"]["title"] for url, t in tombstones().items()})

# 回放同一份快照：lastmod 已相同，全部跳过，索引不变
index = merge(index, upstream_v2)
print("重放 幂等:", report())

# 分片下载：第二片第一次超时，只重取该片
# 分片下载：分片按 URL 列出，fetch 收到分片描述返回该片数据
shards = {
    "shard-1": "https://example.com/sitemap-1.xml",
    "shard-2": "https://example.com/sitemap-2.xml",
}
payloads = {
    "shard-1": {
        "https://example.com/": {"lastmod": "2024-02-01", "title": "首页（改版）"},
        "https://example.com/docs": {"lastmod": "2024-01-05", "title": "文档"},
    },
    "shard-2": {
        "https://example.com/new": {"lastmod": "2024-02-03", "title": "新活动页"},
    },
}
calls = []


def fetch(location):
    calls.append(location)
    # 第二片第一次超时；只重取这一片，另一片不会重下
    if location.endswith("sitemap-2.xml") and calls.count(location) == 1:
        raise IOError("timeout")
    return payloads["shard-2" if "2" in location else "shard-1"]


result = fetch_shards(shards, fetch, retries=1)
print("下载调用:", calls, "尝试次数:", result.attempts)
index = result.merge_into(index)
print("分片合并:", report(), "墓碑数:", report()["tombstones"])
