# driftcheck

现状：单个指标和写死的10%阈值比较，一天一次。

问题：业务量变化后误报，缓慢漂移看不出，周末和促销日天天告警。

目标：历史窗口和同周期基线，支持同比环比、趋势判断和告警合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
