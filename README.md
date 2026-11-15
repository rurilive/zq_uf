# sitemapinc2

现状：每个分片直接覆盖整个索引。

问题：大站全量下载慢，分片互相覆盖，上游删除不消失。

目标：按 lastmod 增量合并，删除留墓碑，分片幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
