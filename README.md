# crawlmetrics

现状：按名字累加计数，快照返回当前值。

问题：分不清请求失败和解析失败，没有耗时和分位数。

目标：分阶段指标、分位数、跨进程合并和重启累加。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
