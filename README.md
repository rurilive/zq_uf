# etagcache

现状：不带条件头，每轮采集都拉全页。

问题：304 被当空页覆盖缓存，缓存没有容量上限。

目标：保存 ETag 和 Last-Modified，304 只更新抓取时间，容量淘汰。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
