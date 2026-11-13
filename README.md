# fingerprint

两级指纹去重，纯标准库。接口：`digest(record)` 返回一级指纹，`store.seen(key)` 返回 bool，`store.check(record)` 返回 `DedupResult(hit, kind, similarity, first_seen)`。

一级（精确）：URL 规范化——scheme/host 小写、去默认端口、去 `utm_*`/`gclid`/`fbclid` 等追踪参数、查询参数排序、路径去尾斜杠、丢 fragment。
二级（近重复）：正文 NFKC 归一后去空白和标点、小写，切 5 字分片做 blake2b 哈希，按 Jaccard 相似度判定，阈值 `threshold` 可配（默认 0.9）。
持久化：`storage_path` 指向的 JSON 文件，格式 `{"version": 1, "entries": [{"url", "shingles", "site", "first_seen"}], "stats": {site: {...}}}`；启动加载，`flush_every` 条一批原子刷盘（tmp + replace），`close()` 兜底。
窗口与统计：`window_seconds` 按 `first_seen` 淘汰内存与文件中的旧条目；`stats()` 按站点返回 checked/url_hits/content_hits/new/hit_rate。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
