# metricrollup2

现状：按处理时间落窗，指标直接累加，分位数取样本平均。

问题：跨窗批次被拆开，迟到重算对不上，分位数失真。

目标：按事件时间上卷，迟到触发重算，分位数用草图合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
