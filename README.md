# metricmerge

现状：按窗口即时累加，迟到数据直接加到当前窗口。

问题：历史值一直错，重算重复累加，分位数直接平均失真。

目标：按事件时间落窗，迟到触发整窗重算，分位数用草图合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
