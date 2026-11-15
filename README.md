# backfill2

现状：整个区间一个任务，直接累加。

问题：大任务跑几天，重复回填翻倍，回填后不对账。

目标：按时间维度切片，先撤销再写，对账差异超阈值停批。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
