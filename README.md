# metriclate2

现状：迟到事件直接累加到当前窗口，重算只回读。

问题：历史值一直错，重算重复累加，跨窗对不上。

目标：按事件时间整窗重算，先撤销再写，重算幂等并保留版本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
