# metricrollup

现状：按自然小时累加，只有计数和求和。

问题：跨小时批次被拆开，迟到重算对不上，分位数直接平均失真。

目标：按事件时间落窗，迟到重算幂等，分位数草图合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
