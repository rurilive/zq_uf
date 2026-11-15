# metricroll3

现状：按给定窗口累加，分位数取样本平均。

问题：跨窗批次被拆开，迟到重算对不上，分位失真。

目标：按事件时间上卷，迟到重算，分位数用草图合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
