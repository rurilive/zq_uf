# metricalert

现状：每条告警立即发送。

问题：上游故障刷屏，同因重复，恢复对不上。

目标：按根因聚合，影响统计，恢复汇总和静默。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
