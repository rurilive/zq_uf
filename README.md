# backfill2

现状：整个区间一个任务，直接累加。

问题：大任务跑几天，重复回填翻倍，回填后不对账。

目标：按时间维度切片，先撤销再写，对账差异超阈值停批。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`plan` 按时间+维度切片并保存复用；`apply` 逐片先撤销再写、
行数/内容校验/原值对账，差异超阈值停批，`trial=True` 小片试跑验证不翻倍；
`retry` 单独重试失败分片，`query`/`export`/`replay` 查询、导出、回放。
