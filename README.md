# crawlmerge

现状：按分片完成顺序覆盖写入。

问题：空值覆盖有效值，旧版本盖新版本，失败无断点。

目标：按主键和版本合并，空值保护，断点续合和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
