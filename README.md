# sitemapinc

现状：全量合并站点地图，新值覆盖旧值。

问题：删除不传播，lastmod 不比较，全量下载慢。

目标：按 lastmod 增量合并，删除墓碑，分片幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
