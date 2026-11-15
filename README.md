# etagcache

条件请求采集缓存：带 ETag / Last-Modified 请求，304 只刷新抓取时间，按站点 LRU 限量。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

- `headers(url)`：有缓存时返回 `If-None-Match` / `If-Modified-Since`，否则返回 `{}`（全量下载）。
- `store(url, response)`：返回 `hit`（本轮是否用了缓存版本）、`not_modified`（是否 304）、`content_updated`（内容是否变化）、`evicted`（本轮淘汰条数）。304 不会覆盖 body。
- `configure(max_per_site, max_total)`：缓存上限；`clear(site=...)` 人工清理并返回按站点的清理数量，清理后下一轮全量下载。
- `stats(site, day)` / `export_stats(path=...)`：按站点、按天的命中统计与 CSV 导出。
- `eviction_records(...)` / `eviction_stats(...)` / `export_evictions(path=...)`：清理记录查询、按站点汇总与导出。
