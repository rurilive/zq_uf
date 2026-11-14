# partitionstats

现状：查询和统计都现场扫描目录。

问题：几万文件很慢，没有缓存，热点看不出。

目标：统计登记，缓存失效，裁剪和耗时输出。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
