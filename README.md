# batchwindow

现状：按自然日切分，迟到不重算。

问题：跨天拆半，历史一直错，重算不幂等。

目标：事件时间窗口，迟到重算，撤销重写和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
