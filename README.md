# datasetcache

现状：按固定一小时TTL过期，失效时整表清空。

问题：上游更新后要等一小时，批量更新把存储打爆，维表改了事实表也清。

目标：按数据集版本和血缘依赖失效，分批限速，支持预热和旧值兜底。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
