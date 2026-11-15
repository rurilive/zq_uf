# qualitytrend

现状：每天用固定阈值判断，超过就告警。

问题：业务量变化就误报，趋势看不出来，告警没有历史对比。

目标：按最近窗口和同周期算基线，缓慢漂移用连续窗口确认，阈值可回测。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
