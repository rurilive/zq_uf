"""演示：条件请求、304 保留内容、LRU 淘汰、清理与统计导出。"""

import csv
import io

from etagcache import (
    cache_size,
    cached,
    clear,
    configure,
    eviction_stats,
    export_evictions,
    export_stats,
    headers,
    store,
)

URL = "https://shop/p"

print("== 第一轮：无条件头，全量下载 ==")
print("headers:", headers(URL))
print("store:  ", store(URL, {"status": 200, "body": "full page",
                              "etag": '"v1"',
                              "last_modified": "Wed, 21 Oct 2015 07:28:00 GMT"}))
first_round = cached(URL)["body"]
print("cached: ", first_round)

print("\n== 第二轮：带上站点给的标识，站点回 304 ==")
print("headers:", headers(URL))
print("store:  ", store(URL, {"status": 304, "body": ""}))
second_round = cached(URL)["body"]
print("cached: ", second_round, "（内容没有被清空）")
print("两轮内容一致:", first_round == second_round == "full page")

print("\n== 容量淘汰：每个站点最多 2 条 ==")
configure(max_per_site=2, max_total=100)
for name in ("a", "b", "c"):
    store(f"https://site1/{name}", {"status": 200, "body": name})
configure(max_per_site=100, max_total=1000)
print("cache_size:", cache_size())
print("淘汰记录:", eviction_stats(site="site1"))

print("\n== 人工清理 site1，下一轮重新全量下载 ==")
print("clear:", clear(site="site1"))
print("headers:", headers("https://site1/a"), "（空 = 全量下载）")

print("\n== 按站点、按天的命中统计 ==")
store(URL, {"status": 304, "body": ""})
for row in export_stats().strip().splitlines():
    print(row)

print("\n== 清理记录导出（可查询、可回看） ==")
for row in csv.DictReader(io.StringIO(export_evictions(site="site1"))):
    print(row)
