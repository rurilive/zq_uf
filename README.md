# failcluster

采集失败归因：把失败聚成可排序的簇报告，接口为 record(failure) 和 report(since=None, until=None)。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 聚类键

- error：错误类型（timeout / parse / selector_miss …）
- site：站点
- selector：失效选择器，可空
- stack：堆栈特征，取前 3 帧「文件名:函数名」忽略行号后的短哈希

四元组相同视为同一根因，跨任务合并为一簇。

## 报告字段

- 顶层：total 总数、last 最近一条；失败数 >= 2 时附 clusters
- 每簇：key、count、affected_tasks/tasks、first_seen/last_seen/duration
- category：sporadic 偶发（<=2 次）、regression 回归（间隔 >=1 小时后复发）、persistent 持续
- samples：每簇最多 3 条样本原文（前两条 + 最新一条）
- 排序：按影响任务数、持续时间降序；report(since, until) 按 epoch 秒时间窗过滤
